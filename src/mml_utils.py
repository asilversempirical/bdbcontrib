from bayeslite import bql_quote_name
from bdbcontrib.population_method import population_method
from enum import Enum
from string import Template

import copy
import json
import jsonschema
import pkgutil
import uuid

MML_SCHEMA = json.loads(
    pkgutil.get_data('bdbcontrib', 'mml.schema.json'))


class StatType(Enum):
    CATEGORICAL = 1
    NUMERICAL = 2
    IGNORE = 3


@population_method(population_to_bdb=0, population_name=1)
def guess_types(bdb, table):
    """Guesses stattypes of a given table.

    Returns a dictionary from column names to the guessed stattype.
    """
    types = {}
    for col in _column_names(bdb, table):
        cursor = bdb.sql_execute(
            'SELECT DISTINCT %s FROM %s'
            % (bql_quote_name(col), bql_quote_name(table)))
        # TODO(asilvers): We don't necessarily need to read all of these in,
        # and for some datasets this may be prohibitive. But it's fine for now.
        vals = {row[0] for row in cursor if row[0] not in [None, '']}
        types[col] = _type_given_vals(vals)
    return types


def _column_names(bdb, table):
    """Returns the column names of a table"""
    with bdb.savepoint():
        cursor = bdb.sql_execute('PRAGMA table_info(%s)'
                                 % (bql_quote_name(table),))
        return list(row[1] for row in cursor)


def _type_given_vals(vals):
    """Returns a guess as to the stattype of a column given its values.

    These heuristics are utterly unprincipled and simply seem to work well
    on a minimal set of test datasets. They should be treated fluidly and
    improved as failures crop up.
    """
    # Constant columns are uninteresting. Do not model them.
    if len(vals) == 1:
        return StatType.IGNORE
    # Even if the whole column is numerical, if there are only a few distinct
    # values they are very likely enums of a sort.
    elif len(vals) < 20:
        return StatType.CATEGORICAL
    elif all(_numbery(v) for v in vals):
        return StatType.NUMERICAL
    elif len(vals) > 1000:
        # That's a lot of values for a categorical.
        # TODO(asilvers): This seems like a reasonable guess, but an
        # explanation in the resulting schema would make this more usable.
        return StatType.IGNORE
    else:
        return StatType.CATEGORICAL


def _numbery(val):
    "Returns True if a value looks like a number."""
    try:
        float(val)
        return True
    except ValueError:
        return False


def to_json(stattypes, metamodel='crosscat'):
    """Returns a json representation of the USING phrase of a
    CREATE GENERATOR call.

    Parameters
    ----------
    stattypes : dict<str, StatType>
        A dictionary from column names to stattypes as produced by guess_types
    metamodel : str, optional
        The metamodel to use, e.g. 'crosscat'

    Returns a json representation validated by MML_SCHEMA.
    """
    cols = {col: {'stattype': typ.name} for col, typ in stattypes.items()}
    mml_json = {
        'metamodel': metamodel,
        'columns': cols}
    jsonschema.validate(mml_json, MML_SCHEMA)
    return mml_json


def to_mml(mml_json, table, generator):
    """Returns a CREATE GENERATOR MML statement which will create a generator
    specified by mml_json.

    Parameters
    ----------
    mml_json
        A json representation of the generator. Must validate against
        MML_SCHEMA
    table : str
        The name of the input table to the generator
    generator : str
        The name of the generator to create
    """
    jsonschema.validate(mml_json, MML_SCHEMA)
    schema_phrase = ','.join(["%s %s" % (bql_quote_name(col), v['stattype'])
                             for col, v in mml_json['columns'].items()
                             if v['stattype'] is not 'IGNORE'])
    subsample = mml_json.get('subsample', None)
    return (Template('CREATE GENERATOR $gen FOR $table '
                     'USING $metamodel($subsample $schema_phrase);')
            .substitute(
                gen=bql_quote_name(generator),
                table=bql_quote_name(table),
                # TODO(asilvers): This can't be quoted, but should be
                # restricted to a known-good set of metamodels.
                metamodel=mml_json['metamodel'],
                subsample='SUBSAMPLE(%d),' % subsample if subsample else '',
                schema_phrase=schema_phrase))


@population_method(population_to_bdb=0, population_name=1)
def validate_schema(bdb, table, mml_json):
    """Returns a modified JSON representation of a generator expression,
    changing the stattypes of any columns which cause issues during analysis
    to IGNORE.

    This creates a single model for each column and analyzes it for a single
    iteration. If this succeeds the column and stattype are deemed good. If it
    fails the stattype is changed to IGNORE and the existing stattype is placed
    into that column's 'guessed' field, overwriting it if it exists.

    Parameters
    ----------
    mml_json
        A json representation of the generator. Must validate against
        MML_SCHEMA
    """
    bad_cols = []
    for col, typ in mml_json['columns'].items():
        # If the column is already ignored there's nothing to check
        if typ['stattype'] is 'IGNORE':
            continue
        one_col_json = copy.deepcopy(mml_json)
        one_col_json['columns'] = {col: typ}
        # Create a temp generator
        gen_name = uuid.uuid4().hex
        try:
            bdb.execute(to_mml(one_col_json, table, gen_name))
            bdb.execute('INITIALIZE 1 MODEL FOR %s'
                        % (bql_quote_name(gen_name),))
            bdb.execute('ANALYZE %s FOR 1 ITERATION WAIT'
                        % (bql_quote_name(gen_name),))
        except AssertionError:
            bad_cols.append(col)
        finally:
            # Drop our temp generator
            bdb.execute('DROP GENERATOR %s' % bql_quote_name(gen_name))
    modified_schema = copy.deepcopy(mml_json)
    # TODO(asilvers): Should we also return a summary of the modifications?
    for col in bad_cols:
        modified_schema['columns'][col]['guessed'] = (
            modified_schema['columns'][col]['stattype'])
        modified_schema['columns'][col]['stattype'] = 'IGNORE'
    jsonschema.validate(mml_json, MML_SCHEMA)
    return modified_schema