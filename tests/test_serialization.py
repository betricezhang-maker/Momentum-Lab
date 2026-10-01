import json
import unittest
import numpy as np
import pandas as pd
from momentumlab.serialization import dumps
from momentumlab.dates import normalize_dates


class SerializationTests(unittest.TestCase):
    def test_nested_missing_scalars_frames_and_dates(self):
        payload=dict(missing=[float('nan'),np.nan,pd.NA,pd.NaT,np.inf,-np.inf],
                     values=(np.int64(2),np.float64(3.5),np.bool_(True)),
                     frame=pd.DataFrame({'retention_rate':[None,.5]}),
                     series=pd.Series([pd.NA],index=['missing']),date=pd.Timestamp('2024-01-02'))
        text=dumps(payload)
        result=json.loads(text,parse_constant=lambda v: self.fail(v))
        self.assertEqual(result['missing'],[None]*6)
        self.assertEqual(result['values'],[2,3.5,True])
        self.assertIsNone(result['frame'][0]['retention_rate'])
        self.assertEqual(result['date'],'2024-01-02T00:00:00')

    def test_mixed_precision_dates(self):
        for precision in ['s','us','ns']:
            values=pd.Series(np.array(['2024-01-02T12:30:00'],dtype=f'datetime64[{precision}]'))
            dates=normalize_dates(values)
            self.assertEqual(str(dates.dtype),'datetime64[ns]')
            self.assertEqual(dates.iloc[0],pd.Timestamp('2024-01-02'))
        self.assertEqual(normalize_dates([20240102,'20240102.0','2024-01-02']).nunique(),1)
