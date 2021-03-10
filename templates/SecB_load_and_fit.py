from pathlib import Path
import numpy as np
from pyhdx import PeptideMasterTable, KineticsFitting, read_dynamx
from pyhdx.fileIO import txt_to_protein

guess = False
epochs = 100000
root_dir = Path().resolve().parent
test_data_dir = root_dir / 'tests' / 'test_data'
input_file_path = test_data_dir / 'ecSecB_apo.csv'

data = read_dynamx(test_data_dir / 'ecSecB_apo.csv', test_data_dir / 'ecSecB_dimer.csv')

pmt = PeptideMasterTable(data, drop_first=1, ignore_prolines=True, remove_nan=False)
pmt.set_control(('Full deuteration control', 0.167))
states = pmt.groupby_state()

series = states['SecB WT apo']

temperature, pH = 273.15 + 30, 8.
kf = KineticsFitting(series, bounds=(1e-2, 800), temperature=temperature, pH=pH)

if guess:
    wt_avg_result = kf.weighted_avg_fit()
    init_guess = wt_avg_result.output
else:
    init_guess = txt_to_protein(test_data_dir / 'ecSecB_guess.txt')

fr_torch = kf.global_fit(init_guess, epochs=epochs, r1=0.1, stop_loss=0.001, patience=100)

