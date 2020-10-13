import pytest
import os
from pyhdx import PeptideMeasurements, PeptideMasterTable, KineticsSeries
from pyhdx.models import Protein
from pyhdx.fileIO import read_dynamx
from pyhdx.support import np_from_txt
from pyhdx.expfact.kint import calculate_kint_for_sequence, calculate_kint_per_residue
import numpy as np
from functools import reduce
from operator import add

directory = os.path.dirname(__file__)


class TestUptakeFileModels(object):

    @classmethod
    def setup_class(cls):
        fpath = os.path.join(directory, 'test_data', 'ecSecB_apo.csv')
        cls.pf1 = PeptideMasterTable(read_dynamx(fpath))

    def test_peptidemastertable(self):
        data = self.pf1.data[self.pf1.data['start'] < 50]
        res = self.pf1.isin_by_idx(self.pf1.data, data)
        assert res.shape == self.pf1.data.shape
        assert len(data) == np.sum(res)

    def test_peptidemeasurement(self):
        assert isinstance(self.pf1, PeptideMasterTable)

        self.pf1.set_control(('Full deuteration control', 0.167))

        states = self.pf1.groupby_state()
        series = states['SecB WT apo']
        assert isinstance(series, KineticsSeries)
        assert series.uniform

    def test_split(self):
        series_name = 'SecB WT apo'

        states = self.pf1.groupby_state()

        series = states[series_name]
        series.make_uniform()

        split_series = series.split()
        new_len = reduce(add, [reduce(add, [len(pm.data) for pm in ks]) for ks in split_series.values()])

        assert len(series.full_data) == new_len

        for k, v in split_series.items():
            s, e = np.array(k.split('_')).astype(int)
            pm = v[0]
            assert np.min(pm.data['start']) == s
            assert np.all(pm.data['end'] < e + 1)


class TestSeries(object):
    @classmethod
    def setup_class(cls):
        fpath = os.path.join(directory, 'test_data', 'ecSecB_apo.csv')
        cls.pf1 = PeptideMasterTable(read_dynamx(fpath))

    def test_coverage(self):
        states = self.pf1.groupby_state()
        series = states['SecB WT apo']
        sequence = 'MFKSTLAAMAAVFALSALSPAAMAAKGDPHVLLTTSAGNIELELDKQKAPVSVQNFVDYVNSGFYNNTTFHRVIPGFMIQGGGFTEQMQQKKPNPPIKNEADNGLRNTRGTIAMARTADKDSATSQFFINVADNAFLDHGQRDFGYAVFGKVVKGMDVADKISQVPTHDVGPYQNVPSKPVVILSAKVLP'
        k_full = calculate_kint_for_sequence(1, len(sequence), sequence, 300, 7)
        k_part = series.cov.calc_kint(300, 7, None)

        # for s1, s2, k1, k2 in zip(sequence, series.cov.sequence, k_full, k_part):
        #     if s2 == 'X':
        #         continue
        #     else:
        #         assert s1 == s2
        #         if s1 == 'P':
        #             continue
        #         assert k1 == k2


class TestSimulatedData(object):
    @classmethod
    def setup_class(cls):
        fpath = os.path.join(directory, 'test_data', 'simulated_data.csv')
        cls.data = np_from_txt(fpath, delimiter=',')
        cls.data['end'] += 1 # because this simulated data is in old format of inclusive, inclusive
        cls.sequence = 'XXXXTPPRILALSAPLTTMMFSASALAPKIXXXXLVIPWINGDKG'

        cls.timepoints = [0.167, 0.5, 1, 5, 10, 30, 100]
        cls.start, cls.end = 5, 46  # total span of protein (inc, excl)
        cls.nc_start, cls.nc_end = 31, 35  # span of no coverage area (inc, inc)

    def test_keep_prolines(self):
        pcf = PeptideMasterTable(self.data, drop_first=0, ignore_prolines=False, remove_nan=False)
        states = pcf.groupby_state()
        assert len(states) == 1
        series = states['state1']
        assert series.uniform
        assert len(series) == len(self.timepoints)
        peptides = series[3]

        assert peptides.start == self.start
        assert peptides.end == self.end
        assert len(peptides.r_number) == self.end - self.start
        assert np.all(np.diff(peptides.r_number) == 1)
        assert peptides.prot_len == self.end - self.start

        blocks = [1, 4, 2, 4, 3, 2, 10, 4, 2, 3, 3, 2, 1]
        assert np.all(blocks == peptides.block_length)

        lengths = peptides.data['end'] - peptides.data['start']
        assert np.all(lengths == peptides.data['ex_residues'])

        block_coverage = [True, True, True, True, True, True, True, False, True, True, True, True, True]
        assert np.all(block_coverage == peptides.block_coverage)

        for row in peptides.X:
            assert np.sum(row) == 1
        assert peptides.X.shape == (len(self.data) / len(self.timepoints), self.end - self.start)

        assert np.all(np.sum(peptides.X, axis=1) == 1)

        for row, elem in zip(peptides.X_red, peptides.data):
            assert np.nansum(row) == len(elem['sequence'])

        assert peptides.X_red.shape == (len(self.data) / len(self.timepoints), len(blocks))
        unique_elems = [np.unique(col[col != 0]) for col in peptides.X_red.T.astype(int)]
        cov_blocks = [elem[0] for elem in unique_elems if len(elem) == 1]
        assert np.all(peptides.block_length[peptides.block_coverage] == cov_blocks)

        assert peptides.block_length[~peptides.block_coverage] == self.nc_end - self.nc_start
        assert np.sum(peptides.has_coverage) == self.nc_start - self.start + self.end - self.nc_end

        assert peptides.exposure == self.timepoints[3]
        assert peptides.state == 'state1'
        assert peptides.sequence == self.sequence

        # series keys are inclusive, exclusive
        keys = [f'{self.start}_{self.nc_start}', f'{self.nc_end}_{self.end}']
        split = series.split()

        for k1, k2 in zip(keys, split.keys()):
            assert k1 == k2

        s1 = split[keys[0]]
        p1 = s1[3]
        assert p1.start == self.start
        assert p1.end == self.nc_start
        assert np.all(p1.r_number == np.arange(self.start, self.nc_start))

        s2 = split[keys[1]]
        p2 = s2[3]
        assert p2.start == self.nc_end
        assert p2.end == self.end
        assert np.all(p2.r_number == np.arange(self.nc_end, self.end))

        for i, t in enumerate(self.timepoints):
            # Check all timepoints in both split series
            assert s1[i].exposure == t
            assert s2[i].exposure == t

            total_peptides = np.sum([len(v[i].data) for v in split.values()])
            assert total_peptides == len(peptides.data)

    def test_drop_first_prolines(self):
        for i, df in enumerate([1, 2, 3]):
            print('df', df)
            pcf = PeptideMasterTable(self.data, drop_first=df, ignore_prolines=True, remove_nan=False)
            states = pcf.groupby_state()
            assert len(states) == 1
            series = states['state1']
            assert series.uniform
            assert len(series) == len(self.timepoints)

            peptides = series[3]
        #    assert peptides.start == self.start + df + 2 # 2 prolines
            assert peptides.end == self.end

            #This still includes peptides
            assert peptides.sequence[peptides.start:].lstrip('X') == self.sequence[peptides.start:]

            #take only the exchanging residues
            ex_res = ''.join(list(peptides.sequence[i] for i in peptides.r_number - 1))
            # this only holds up to the first coverage break
            assert ex_res[:10] == self.sequence[peptides.start - 1:].replace('P', '')[:10]


            assert np.sum(peptides.block_length) == len(peptides.r_number)
            reductions = [
                [4, 3, 2, 3, 2, 2, 1],
                [4, 3, 3, 4, 3, 2, 2],
                [4, 4, 4, 5, 4, 3, 3]
            ][i]

            #unmodified: [11 15  9 19  8  9  5]
            lengths = np.array([len(seq) for seq in peptides.data['sequence']]) - np.array(reductions)
            assert np.all(lengths == peptides.data['ex_residues'])


class TestCoverage(object):
    @classmethod
    def setup_class(cls):
        fpath = os.path.join(directory, 'test_data', 'simulated_data_uptake.csv')
        data = read_dynamx(fpath)
        cls.sequence = 'XXXXTPPRILALSAPLTTMMFSASALAPKIXXXXLVIPWINGDKG'

        timepoints = [0.167, 0.5, 1, 5, 10, 30, 100]
        start, end = 5, 45  # total span of protein (inc, inc)
        nc_start, nc_end = 31, 34  # span of no coverage area (inc, inc)

        pmt = PeptideMasterTable(data, drop_first=1, ignore_prolines=True, remove_nan=False)
        pmt.set_backexchange(0.)
        states = pmt.groupby_state()
        cls.series = states['state1']
        cls.series.make_uniform()


class TestProtein(object):
    @classmethod
    def setup_class(cls):

        dtype = [('r_number', int), ('apple', float)]
        array1 = np.empty(15, dtype=dtype)
        array1['r_number'] = np.arange(15) + 3
        array1['apple'] = np.ones(15) * 12
        cls.array1 = array1

        dtype = [('r_number', int), ('apple', float), ('grapes', float)]
        array2 = np.empty(17, dtype=dtype)
        array2['r_number'] = np.arange(17) + 6
        array2['apple'] = np.ones(17) * 10
        array2['grapes'] = np.ones(17) * 15 + np.random.rand(17)
        cls.array2 = array2


        dtype = [('r_number', int), ('pear', float), ('banana', float)]
        array3 = np.empty(10, dtype=dtype)
        array3['r_number'] = np.arange(10) + 1
        array3['pear'] = np.random.rand(10) + 20
        array3['banana'] = -(np.random.rand(10) + 20)
        cls.array3 = array3

    def test_artithmetic(self):
        p1 = Protein(self.array1, index='r_number')
        p2 = Protein(self.array2, index='r_number')

        comparison_quantity = 'apple'
        datasets = [self.array1, self.array2]  # len 15, 3 tm 17 , len 17, 6 tm 22
        r_all = np.concatenate([array['r_number'] for array in datasets])
        r_full = np.arange(r_all.min(), r_all.max() + 1)

        output = np.full_like(r_full, fill_value=np.nan,
                              dtype=[('r_number', int), ('value1', float), ('value2', float), ('comparison', float)])

        output['r_number'] = r_full
        idx = np.searchsorted(output['r_number'], self.array1['r_number'])
        output['value1'][idx] = self.array1[comparison_quantity]

        idx = np.searchsorted(output['r_number'], self.array2['r_number'])
        output['value2'][idx] = self.array2[comparison_quantity]

        comparison = p1 - p2
        output['comparison'] = output['value1'] - output['value2']
        assert np.allclose(comparison['apple'], output['comparison'], equal_nan=True)

        comparison = p1 + p2
        output['comparison'] = output['value1'] + output['value2']
        assert np.allclose(comparison['apple'], output['comparison'], equal_nan=True)

        comparison = p1 / p2
        output['comparison'] = output['value1'] / output['value2']
        assert np.allclose(comparison['apple'], output['comparison'], equal_nan=True)

        comparison = p1 * p2
        output['comparison'] = output['value1'] * output['value2']
        assert np.allclose(comparison['apple'], output['comparison'], equal_nan=True)