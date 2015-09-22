# -*- coding: utf-8 -*-

#   Copyright (c) 2010-2014, MIT Probabilistic Computing Project
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

import itertools
import importlib
import math

import numpy as np

from bayeslite.core import *
from bayeslite.exception import BQLError
from bayeslite.sqlite3_util import sqlite3_quote_name as quote
from bayeslite.util import logsumexp, logmeanexp

from crosscat.utils import sample_utils as su

import bayeslite.metamodel
import bayeslite.bqlfn

import bdbcontrib
from bdbcontrib.foreign.keplers_law import KeplersLaw
from bdbcontrib.foreign.random_forest import RandomForest

composer_schema_1 = '''
INSERT INTO bayesdb_metamodel
    (name, version) VALUES ('composer', 1);

CREATE TABLE bayesdb_composer_cc_id(
    genid INTEGER NOT NULL PRIMARY KEY
        REFERENCES bayesdb_generator(id),
    crosscat_genid INTEGER NOT NULL
        REFERENCES bayesdb_generator(id)
);
'''

class Composer(bayeslite.metamodel.IBayesDBMetamodel):
    """A metamodel which composes foreign predictors with CrossCat.
    """

    def __init__(self):
        # Make an account of all data structures.
        self.cc_id = None   # id of internal CC.
        self.cc = None      # instance of cc metamodel.
        self.fcols = None   # dict, key = foreign col, vals = list of parents.
        self.lcols = None   # list of local cols.
        self.topo = None    # Same ase self.fcols but keys sorted topologically.
        self.get_fp = None  # dict, key = foreign col, val = FP instance.

    def name(self):
        """Return the name of the metamodel as a string.
        """
        return 'composer'

    def register(self, bdb):
        # XXX TODO: Figure out a strategy for serialization.
        # XXX Causes serialization problem, should have v0 of schema which
        # is only ever before the first time this metamodel is registered
        # in the history of the bdb.
        # for stmt in composer_schema_1.split(';'):
            # bdb.sql_execute(stmt)
        return

    def create_generator(self, bdb, table, schema, instantiate):
        # TODO: Parse all this information from the schema.
        schema = [
            ('Country_of_Operator', 'CATEGORICAL'),
            ('Operator_Owner', 'CATEGORICAL'),
            ('Users', 'CATEGORICAL'),
            ('Purpose', 'CATEGORICAL'),
            ('Class_of_orbit', 'CATEGORICAL'),
            ('Perigee_km', 'NUMERICAL'),
            ('Apogee_km', 'NUMERICAL'),
            ('Eccentricity', 'NUMERICAL'),
            ('Launch_Mass_kg', 'NUMERICAL'),
            ('Dry_Mass_kg', 'NUMERICAL'),
            ('Power_watts', 'NUMERICAL'),
            ('Date_of_Launch', 'NUMERICAL'),
            ('Anticipated_Lifetime', 'NUMERICAL'),
            ('Contractor', 'CATEGORICAL'),
            ('Country_of_Contractor', 'CATEGORICAL'),
            ('Launch_Site', 'CATEGORICAL'),
            ('Launch_Vehicle', 'CATEGORICAL'),
            ('Source_Used_for_Orbital_Data', 'CATEGORICAL'),
            ('longitude_radians_of_geo', 'NUMERICAL'),
            ('Inclination_radians', 'NUMERICAL'),
            ('Type_of_Orbit', 'CATEGORICAL'),
            ('Period_minutes', 'NUMERICAL')
        ]

        # Maps FP target columns (fcol) to their conditions columns .
        # Currenlty strings, but will be mapped to columns after instantiate.
        fcols = {
            # Orbital Mechanics
            'Period_minutes' : ['Apogee_km', 'Perigee_km'],
            # Random Forest
            'Type_of_Orbit' : ['Apogee_km', 'Perigee_km', 'Eccentricity',
                'Period_minutes', 'Launch_Mass_kg', 'Power_watts',
                'Anticipated_Lifetime', 'Class_of_orbit']
        }

        # Maps target FP columns to module for initializing FP objects
        # Will be replaced by actual object when initialize is called.
        get_fp = {
            'Type_of_Orbit' : importlib.import_module('random_forest'),
            'Period_minutes' : importlib.import_module('keplers_law')
        }

        # locls = local columns modeled by CrossCat.
        lcols = [(col,stat) for (col,stat) in schema if col not in fcols]

        # Instantiate **this** generator.
        genid, columns = instantiate(schema)

        # Convert all strings to to BayesDB column numbers.
        self.fcols = {}
        self.get_fp = {}
        for f in fcols:
            fcolno = bayesdb_generator_column_number(bdb, genid, f)
            self.fcols[fcolno] = [bayesdb_generator_column_number(bdb,
                genid, col) for col in fcols[f]]
            self.get_fp[fcolno] = get_fp[f]

        # Convert lcols from strings to BayesDB column numbers.
        self.lcols = [bayesdb_generator_column_number(bdb, genid, col[0])
            for col in lcols]

        # Sort fcols in topological order.
        self.topo = self.topolgical_sort(self.fcols)

        # Create internal crosscat generator.
        self.cc_name = bayeslite.core.bayesdb_generator_name(bdb,
            genid) + '_cc'
        ignore = ','.join(['{} IGNORE'.format(pair[0]) for pair in schema
            if pair[0] in fcols])
        cc = ','.join(['{} {}'.format(pair[0], pair[1]) for pair in lcols])
        # bql = """
        #     CREATE GENERATOR {} FOR satellites USING crosscat(
        #         GUESS(*), Name IGNORE,
        #         {} ,
        #         {}
        #     );
        # """.format(self.cc_name, ignore, cc)
        # bdb.execute(bql)

        # Obtain the genid of internal cc, and the metamodel object.
        # Should be stored in the database table bayesdb_fp_composer_cc_lookup
        self.cc_name = 'satcc'
        self.cc_id = bayesdb_get_generator(bdb, 'satcc')
        self.cc = bayesdb_generator_metamodel(bdb, self.cc_id)

    def drop_generator(self, bdb, genid):
        raise NotImplementedError('Composer generators cannot be dropped. '
            'Feature coming soon.')

    def initialize_models(self, bdb, genid, modelnos, model_config):
        # Initialize internal Crosscat. If k models of Composer are instantiated
        # then k internal CC models will be created, with a 1-1 mapping.
        # bql = """
        #     INITIALIZE {} MODELS FOR {};
        #     """.format(len(modelnos), self.cc_name)
        # bdb.execute(bql)

        # Obtain the dataframe for foreign predictors.
        df = bdbcontrib.cursor_to_df(bdb.execute('''
            SELECT * FROM {}
            '''.format(bayesdb_generator_table(bdb, genid))))

        # Initialize the foriegn predictors.
        for f_target, f_conditions in self.fcols.items():
            # Convert column numbers to names.
            targets = \
                [(bayesdb_generator_column_name(bdb, genid, f_target),
                bayesdb_generator_column_stattype(bdb, genid,f_target))]
            conditions = \
                [(bayesdb_generator_column_name(bdb, genid, f_c),
                bayesdb_generator_column_stattype(bdb, genid, f_c))
                for f_c in f_conditions]

            # Overwrite the module with an actual trained FP instance.
            initializer = self.get_fp[f_target]
            self.get_fp[f_target] = initializer.create_predictor(df, targets,
                conditions)


    def drop_models(self, bdb, genid, modelnos=None):
        raise NotImplementedError('Composer generator models cannot be '
            'dropped. Feature coming soon.')


    def analyze_models(self, bdb, genid, modelnos=None, iterations=1,
                max_seconds=None, ckpt_iterations=None, ckpt_seconds=None):
        # XXX The composer currently does not perform joint inference.
        # (Need full GPM interface, active research project).
        self.cc.analyze_models(bdb, self.cc_id, modelnos=modelnos,
            iterations=iterations, max_seconds=max_seconds,
            ckpt_iterations=ckpt_iterations, ckpt_seconds=ckpt_seconds)


    def column_dependence_probability(self, bdb, genid, modelno, colno0,
                colno1):
        # XXX Aggregator only.
        if modelno is None:
            n_model = 1
            while bayesdb_generator_has_model(bdb, genid, n_model):
                n_model += 1
            p = sum(self._column_dependence_probability(bdb, genid, m, colno0,
                colno1) for m in xrange(n_model)) / float(n_model)
        else:
            p = self._column_dependence_probability(bdb, genid, modelno, colno0,
                colno1)
        return p


    def _column_dependence_probability(self, bdb, genid, modelno, colno0,
            colno1):
        # XXX Computes the dependence probability for a single model.
        if modelno is None:
            raise ValueError('Invalid modelno argument for '
                'internal _column_dependence_probability. An integer modelno '
                'is required, not None.')

        # Trivial case.
        if colno0 == colno1:
            return 1

        # Determine which object modeled colno0, colno1
        c0_foreign = colno0 in self.fcols
        c1_foreign = colno1 in self.fcols

        # If neither col is foreign, delegate to CrossCat.
        if not (c0_foreign or c1_foreign):
            return self.cc.column_dependence_probability(bdb,
                self.cc_id, modelno,
                self._internal_cc_colno(bdb, genid, colno0),
                self._internal_cc_colno(bdb, genid, colno1))

        # (colno0, colno1) form a (target, given) pair.
        # wE explicitly modeled them as dependent by assumption.
        # TODO: Strong assumption? What if FP determines it is not
        # dependent on one of its conditions? (ie 0 coeff in regression)
        if colno0 in self.fcols.get(colno1, []) or \
                colno1 in self.fcols.get(colno0, []):
            return 1

        # (colno0, colno1) form a local, foreign pair.
        # IF [col0 FP target], [col1 CC], and [all conditions of col0 IND col1]
        #   then [col0 IND col1].
        # XXX The reverse is not true generally (counterxample), but we shall
        # assume an IFF condition. This assumption is not unlike the transitive
        # closure property of independence in CrossCat.
        if (c0_foreign and not c1_foreign) or \
                (c1_foreign and not c0_foreign):
            fcol = colno0 if c0_foreign else colno1
            lcol = colno1 if c0_foreign else colno0
            return any(self._column_dependence_probability(bdb, genid,
                modelno, cond, lcol) for cond in self.fcols[fcol])

        # XXX TODO: Determine independence semantics for this case.
        # Both columns are foreign. (Recursively) return 1 if any of their
        # conditions (possibly FPs) have dependencies.
        assert c0_foreign and c1_foreign
        return any(self._column_dependence_probability(bdb, genid, modelno,
            cond0, cond1)
            for cond0 in self.fcols[colno0]
            for cond1 in self.fcols[colno1])


    def column_mutual_information(self, bdb, genid, modelno, colno0, colno1,
            numsamples=100):
        # TODO: Allow conditional mutual information.

        # Two cc columns, delegate.
        if colno0 in self.lcols and colno1 in self.lcols:
            cc_colnos = self._internal_cc_colnos(bdb, genid,
                [colno0, colno1])
            return self.cc.column_mutual_information(bdb, self.cc_id, modelno,
                cc_colnos[0], cc_colnos[1], numsamples=numsamples)

        # Simple Monte Carlo by simulating from the joint
        # distributions, and computing the joint and marginal densities.
        Q, Y = [colno0, colno1], []
        samples = self.simulate(bdb, genid, modelno, Y, Q,
            numpredictions=numsamples)
        mi = logpx = logpy = logpxy = 0
        for s in samples:
            Qx = [(colno0, s[0])]
            logpx = self._joint_logpdf(bdb, genid, modelno, Qx, [])
            Qy = [(colno1, s[1])]
            logpy = self._joint_logpdf(bdb, genid, modelno, Qy, [])
            Qxy = Qx+Qy
            logpxy = self._joint_logpdf(bdb, genid, modelno, Qxy, [])
            mi += logpx - logpx - logpy
        # TODO: Use linfoot?
        return mi


    def column_value_probability(self, bdb, genid, modelno, colno, value,
            constraints):
        # XXX Aggregator only.
        p = []
        if modelno is None:
            n_model = 0
            while bayesdb_generator_has_model(bdb, genid, n_model):
                p.append(self._joint_logpdf(bdb, genid, n_model,
                    [(colno, value)], constraints))
                n_model += 1
            p = logmeanexp(p)
        else:
            p = self._joint_logpdf(bdb, genid, modelno, [(colno, value)],
                constraints)
        return math.exp(p)


    def _joint_logpdf(self, bdb, genid, modelno, Q, Y):
        # XXX Computes the joint probability of query Q given evidence Y
        # for a single model. This function is an integrator over
        # _forward_joint_logpdf using importance-weighted samples from
        # simulate(Q|Y).
        if modelno is None:
            raise ValueError('Invalid modelno argument for '
                'internal _joint_logpdf. An integer modelno '
                'is required, not None.')

        # Optimization. All cols in Q and Y local, delegate to CC.
        if self._all_local(bdb, genid, Q, Y):
            Y_cc = [(self._internal_cc_colno(bdb, self.cc_id, c),v)
                for c, v in Y]
            Q_cc = [(self._internal_cc_colno(bdb, self.cc_id, c),v)
                for c, v in Q]
            logpdf = self._joint_logpdf_cc(bdb, self.cc_id, modelno, Q_cc, Y_cc)
            return math.exp(logpdf)

        # Estimate the integral using self-normalized importance sampling.
        # TODO: Discuss strategy
        #  -- simulate gives higher quality (unweighted) samples but slower.
        #  -> _forward_weighted_sample gives lower qualty samples but faster.
        samples, weights = self._forward_weighted_sample(bdb, genid, modelno, Y,
            n_samples=None)

        probs = []
        for s in samples:
            for c,v in Q:
                del s[c]
            p = self._forward_joint_logpdf(bdb, genid, modelno, Q, s.items())
            probs.append(p)

        # TODO: Verify this is procedure numerically correct.
        # Self normalize the p's by importance weights w.
        # weights = [logw1 logw2 ... logwk], probs=[logp1 logp2 ... logpk]
        # Compute N = log(w1p1 + w2p2 + ... wkpk)
        N = logsumexp(np.asarray(weights) + np.asarray(probs))
        D = logsumexp(np.asarray(weights))
        return N-D


    def predict_confidence(self, bdb, genid, modelno, colno, rowid,
            numsamples=None):
        # Predicts a value for the cell [rowid, colno] with a confidence metric.

        # XXX Prefer accuracy over speed for imputation.
        if numsamples is None:
            numsamples = 50

        # Obtain all values for all other columns.
        colnos = bayesdb_generator_column_numbers(bdb, genid)
        colnames = bayesdb_generator_column_names(bdb, genid)
        table = bayesdb_generator_table(bdb, genid)
        select_sql = '''
            SELECT {} FROM {} WHERE _rowid_ = ?
        '''.format(','.join(map(quote, colnames)), quote(table))
        cursor = bdb.sql_execute(select_sql, (rowid,))
        row = None
        try:
            row = cursor.next()
        except StopIteration:
            generator = bayesdb_generator_table(bdb, genid)
            raise BQLError(bdb, 'No such row in table {}'
                ' for generator {}: {}'.format(table, generator, rowid))

        # Account for multipling imputations if imputing parents.
        parent_conf = 1

        # Predicting local column.
        if colno in self.lcols:
            # Delegate to CC iff
            # (lcol has no children OR all its children are None).
            children = [f for f in self.fcols if colno in self.fcols[f]]
            if len(children) == 0 or \
                    all(row[i] is None for i in xrange(len(row)) if i+1
                        in children):
                return self.cc.predict_confidence(bdb, self.cc_id, modelno,
                    self._internal_cc_colno(bdb, genid, colno), rowid)
            else:
                # Obtain likelihood weighted samples from posterior.
                Q = [colno]
                Y = [(c,v) for c,v in zip(colnos, row) if c != colno and v
                        is not None]
                samples = self.simulate(bdb, genid, modelno, Y, Q,
                    numpredictions=numsamples)
                samples = [s[0] for s in samples]
        # Predicting foreign column.
        else:
            conditions = {c:v for c,v in zip(colnames, row) if
                bayesdb_generator_column_number(bdb, genid, c) in
                self.fcols[colno]}
            for colname, val in conditions.iteritems():
                # Impute all missing parnets.
                if val is None:
                    imp_col = bayesdb_generator_column_number(bdb, genid, colname)
                    imp_val, imp_conf = self.predict_confidence(bdb, genid,
                        modelno, imp_col, rowid, numsamples=numsamples)
                    # XXX If imputing several parents, take the overall
                    # overall conf of a collection of imputations as the min
                    # conf. If we define imp_conf as P[imp_val = correct]
                    # then we might choose to multiply the imp_confs, but we
                    # cannot assert apriori that the imp_confs themselves are
                    # independent so multiplying is extremely conservative.
                    parent_conf = min(parent_conf, imp_conf)
                    conditions[colname] = imp_val
            assert all(v is not None for c,v in conditions.iteritems())
            samples = self.get_fp[colno].simulate(numsamples, conditions)

        # Since a foreign predictor does not know how to impute, imputation
        # shall occur here in the composer by simulate/logpdf calls.
        stattype = bayesdb_generator_column_stattype(bdb, genid, colno)
        if stattype == 'categorical':
            # Find most common value in samples.
            imp_val =  max(((val, samples.count(val)) for val in set(samples)),
                key=lambda v: v[1])[0]
            # fcol -> query pdf, lcol -> take fraction.
            imp_conf = math.exp(self.get_fp[colno].logpdf(imp_val,
                conditions)) if colno in self.fcols else \
                sum(np.array(samples)==imp_val) / len(samples)

        elif stattype == 'numerical':
            # XXX The definition of confidence is P[k=1] where
            # k=1 is the number of mixture componets (we need a distribution
            # over GPMM to answer this question). The confidence is instead
            # implemented as \max_i{p_i} where p_i are the weights of a
            # fitted DPGMM.
            imp_val = np.mean(samples)
            imp_conf = su.continuous_imputation_confidence(samples,
                None, None, n_steps=1000)

        else:
            raise ValueError('Unknown stattype {} for a foreign predictor '
                'column encountered in predict_confidence.'.format(stattype))

        return imp_val, imp_conf * parent_conf


    def simulate(self, bdb, genid, modelno, constraints, colnos,
            numpredictions=1):
        # Optimization; all cols local, just delegate to CC.
        if self._all_local(bdb, genid, colnos, constraints, Qv=False):
            cc_constraints = [(self._internal_cc_colno(bdb, self.cc_id, c), v)
                for c, v in constraints]
            cc_targets = self._internal_cc_colnos(bdb, genid, colnos)
            return self.cc.simulate(bdb, self.cc_id, modelno,
                cc_constraints, cc_targets, numpredictions=numpredictions)

        # Solve inference problem by sampling-importance sampling.
        result = []
        for i in xrange(numpredictions):
            ll_samples, weights = self._forward_weighted_sample(bdb, genid,
                modelno, constraints)
            p = np.exp(np.asarray(weights) - np.max(weights))
            p /= np.sum(p)
            draw = np.nonzero(np.random.multinomial(1,p))[0][0]
            result.append([ll_samples[draw].get(col) for col in colnos])

        return result

    def row_similarity(self, bdb, genid, modelno, rowid, target_rowid,
            colnos):
        # XXX Delegate to CrossCat.
        cc_colnos = self._internal_cc_colnos(bdb, genid, colnos)
        return self.cc.row_similarity(bdb, self.cc_id, modelno, rowid,
            target_rowid, cc_colnos)


    def row_column_predictive_probability(self, bdb, genid, modelno,
            rowid, colno):
        raise NotImplementedError('PREDICTIVE PROBABILITY is being retired. '
            'Please use PROBABILITY OF <[...]> [GIVEN [...]] instead.')


    def _weighted_sample(self, bdb, genid, modelno, Y, n_samples=None):
        # Returns a list of [(sample, weight), ...]
        # Each `sample` is the a vector s=(X1,...,Xn) of values for all nodes in
        # the network. Y specifies evidence nodes: all returned samples have
        # constrained values at the evidence nodes.
        # `weight` is the likelihood of the evidence Y under s\Y.
        if n_samples is None:
            n_samples = 100

        # Create n_samples dicts, each entry is weighted sample from joint.
        samples = [{c:v for (c,v) in Y} for _ in xrange(n_samples)]
        weights = []
        w0 = 0

        # Assess likelihood of evidence at root.
        cc_evidence = [(c, v) for c,v in Y if c in self.lcols]
        if cc_evidence:
            w0 += self._joint_logpdf_cc(bdb, genid, modelno, cc_evidence, [])
        # Simulate latent CCs.
        cc_latent = [c for c in self.lcols if c not in samples[0]]
        cc_simulated = self.cc.simulate(bdb, self.cc_id, modelno, cc_evidence,
            cc_latent, numpredictions=n_samples)
        for k in xrange(n_samples):
            w = w0
            # Add simulated lcols.
            samples[k].update({c:v for c,v in zip(cc_latent, cc_simulated[k])})
            for f, _ in self.topo:
                # Assert all parents of FP known (evidence or simulated).
                assert set(self.fcols[f]).issubset(set(samples[k]))
                conditions = {bayesdb_generator_column_name(bdb, genid, c):v
                    for c,v in samples[k].iteritems() if c in self.fcols[f]}
                # f is evidence, compute likelihood weight.
                if f in samples[k]:
                    w += self.get_fp[f].logpdf(samples[k][f], conditions)
                # f is latent, simulate from conditional distribution.
                else:
                    f_simulated = self.get_fp[f].simulate(1, conditions)
                    samples[k][f] = f_simulated[0]
            weights.append(w)

        return samples, weights


    # TODO migrate to a reasonable place (ie sample_utils in CrossCat).
    def _joint_logpdf_cc(self, bdb, genid, modelno, Q, Y):
        # Computes the joint probability of CrossCat columns. Q=[(col,val)...]
        # is the query, and Y=[(col,val)...] is the evidence.

        # Ensure consistency for nodes in both query and evidence.
        ignore = set()
        for (cq, vq), (cy, vy) in itertools.product(Q, Y):
            if cq not in self.lcols and cy not in self.lcols:
                raise ValueError('Foreign colno encountered in internal '
                    '_joint_logpdf_cc. Only local colnos may be specified in '
                    'the queries Q and constraints Y.')
            if cq == cy:
                if vq == vy:
                    ignore.add(cq)
                else:
                    return -float('inf')

        Qi = []
        for (col, val) in Q:
            if col not in ignore:
                Qi.append((self._internal_cc_colno(bdb, genid, col),val))
        Yi = []
        for (col, val) in Y:
            Yi.append((self._internal_cc_colno(bdb, genid, col), val))

        prob = 0
        for (col, val) in Qi:
            r = self.cc.column_value_probability(bdb, self.cc_id, modelno, col,
                val, Yi)
            if r == 0:
                return -float('inf')
            prob += math.log(r)
            Yi.append((col,val))

        return prob


    # TODO: Move this to somewhere reasonable?
    def topolgical_sort(self, graph):
        """Topologically sort a directed graph represented as an adjacency list.
        Assumes that edges are incoming, ie (10:[8,7]) means 8->10 and 7->10.

        Parameters
        ----------
        graph : dict or list
            Adjacency list or dict representing the graph, for example
                graph_d = {10: [8, 7], 5: [8, 7, 9, 10, 11, 13, 15]}
                graph_l = [(10, [8, 7]), (5, [8, 7, 9, 10, 11, 13, 15])]

        Returns
        -------
        graph_sorted : list
            An adjacency list, where the order of the nodes is listed
            in topological order.
        """
        graph_sorted = []
        graph = dict(graph)

        # Run until the unsorted graph is empty.
        while graph:
            acyclic = False
            for node, edges in graph.items():
                for edge in edges:
                    if edge in graph:
                        break
                else:
                    acyclic = True
                    del graph[node]
                    graph_sorted.append((node, edges))

            if not acyclic:
                raise RuntimeError('A cyclic dependency occurred in '
                    'topological_sort.')

        return graph_sorted


    # TODO: Convert to SQL table queries on bayesdb_fp_composer_cc_lookup.
    def _internal_cc_colno(self, bdb, genid, colno):
        return self._internal_cc_colnos(bdb, genid, [colno])[0]


    def _internal_cc_colnos(self, bdb, genid, colnos):
        # First get the names from this generator.
        colnames = [bayeslite.core.bayesdb_generator_column_name(bdb,
            genid, no) for no in colnos]
        # Now do the inverse mapping.
        return [bayeslite.core.bayesdb_generator_column_number(bdb, self.cc_id,
            name) for name in colnames]


    def _internal_cc_id(self, bdb, genid):
        sql = '''
            SELECT crosscat_genid
                FROM bayesdb_composer_cc_id
                WHERE genid = ?
        '''
        return bdb.sql_execute(sql, (genid,)).fetchall()[0][0]
