import pandas as pd
engine = 'pyxlsb'
xls = pd.ExcelFile('SU-RSA MASTER.xlsb', engine=engine)

print("=== MAIN Sheet IDs ===")
main_ids = set()
df_main = pd.read_excel(xls, sheet_name='MAIN', header=0)
for _, r in df_main.head(20).iterrows():
    val = str(r.iloc[0]).strip()
    if val and val.lower() != 'nan': main_ids.add(val)
print(list(main_ids)[:10])

print("\n=== 1C TASK LIST IDs ===")
t_ids = set()
df = pd.read_excel(xls, sheet_name='1C TASK LIST', header=None)
for idx, row in df.head(15).iterrows():
    for c in row.values:
        if isinstance(c, str) and '-' in c and not '/' in c and len(c) > 6:
            t_ids.add(c)
print(list(t_ids)[:10])
