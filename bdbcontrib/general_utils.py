import pandas as pd
import bayeslite.core
from bayeslite.sqlite3_util import sqlite3_quote_name


def cursor_to_df(cursor):
    """ Converts SQLite3 cursor to a pandas DataFrame """
    df = pd.DataFrame.from_records(cursor.fetchall(), coerce_float=True)
    df.columns = [desc[0] for desc in cursor.description]
    for col in df.columns:
        try:
            df[col] = df[col].astype(float)
        except ValueError:
            pass

    return df


def get_column_info(bdb, generator_name):
    generator_id = bayeslite.core.bayesdb_get_generator(bdb, generator_name)
    sql = '''
        SELECT c.colno, c.name, gc.stattype
            FROM bayesdb_generator AS g,
                bayesdb_generator_column AS gc,
                bayesdb_column AS c
            WHERE g.id = ?
                AND gc.generator_id = g.id
                AND gc.colno = c.colno
                AND c.tabname = g.tabname
            ORDER BY c.colno
        '''
    cursor = bdb.sql_execute(sql, (generator_id,))
    column_info = cursor.fetchall()
    return column_info


def get_data_as_list(bdb, table_name, column_list=None):
    if column_list is None:
        sql = 'SELECT * FROM {};'.format(sqlite3_quote_name(table_name))
    else:
        sql = 'SELECT {} FROM {}'.format(', '.join(map(sqlite3_quote_name, column_list)), table_name)
    cursor = bdb.sql_execute(sql)
    T = cursor_to_df(cursor).values.tolist()
    return T


def get_shortnames(bdb, table_name, column_names):
    return get_column_descriptive_metadata(bdb, table_name, column_names, 'shortname')


def get_descriptions(bdb, table_name, column_names):
    return get_column_descriptive_metadata(bdb, table_name, column_names, 'description')


def get_column_descriptive_metadata(bdb, table_name, column_names, md_field):
    short_names = []
    # XXX: this is indefensibly wasteful.
    bql = '''
        SELECT colno, name, {}
            FROM  bayesdb_column
            WHERE tabname = ?
        '''.format(md_field)
    curs = bdb.sql_execute(bql, (table_name,))
    records = curs.fetchall()
    for cname in column_names:
        for record in records:
            if record[1] == cname:
                sname = record[2]
                if sname is None:
                    sname = cname
                    print 'Warning: No {} found for {}. Using column name.'.format(md_field, cname)
                short_names.append(sname)
                break

    if len(short_names) != len(column_names):
        import pdb
        pdb.set_trace()
    return short_names
