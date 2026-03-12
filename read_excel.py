import pandas as pd
import json
import sys

try:
    print("Reading Excel (.xlsb)...")
    xls = pd.ExcelFile('SU-RSA MASTER.xlsb', engine='pyxlsb')
    res = {"sheets": xls.sheet_names, "data": {}}
    
    for sheet in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet)
        res["data"][sheet] = {
            "columns": [str(c) for c in df.columns],
            "sample": df.head(3).astype(str).to_dict('records')
        }
        
    with open('excel_struct.json', 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("Success")
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
