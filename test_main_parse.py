import pandas as pd
import re

def parse_intervals(interval_str):
    if not isinstance(interval_str, str):
        return None, None, None
    fh, fc, dy = None, None, None
    
    # 32000 FH
    if 'FH' in interval_str.upper():
        nums = re.findall(r'\d+', interval_str)
        if nums: fh = float(nums[0])
    # 24000 FC
    if 'FC' in interval_str.upper():
        nums = re.findall(r'\d+', interval_str)
        if nums: fc = int(nums[0])
    # 12 YE or 24 MO or 120 DY
    if 'YE' in interval_str.upper() or 'YR' in interval_str.upper() or 'YEAR' in interval_str.upper():
        nums = re.findall(r'\d+', interval_str)
        if nums: dy = int(nums[0]) * 365
    elif 'MO' in interval_str.upper():
        nums = re.findall(r'\d+', interval_str)
        if nums: dy = int(nums[0]) * 30
    elif 'DY' in interval_str.upper() or 'DAY' in interval_str.upper():
        nums = re.findall(r'\d+', interval_str)
        if nums: dy = int(nums[0])
        
    return fh, fc, dy

df = pd.read_excel('SU-RSA MASTER.xlsb', sheet_name='MAIN', engine='pyxlsb')
print("MAIN Columns:", df.columns.tolist()[:10])

# MPD ITEM NUMBER is at col 0, INTERVAL are col 1 and 2
for idx, row in df.head(5).iterrows():
    mpd = str(row.iloc[0]).strip()
    if mpd != 'nan' and 'MPD' not in mpd.upper():
        int1 = str(row.iloc[1])
        int2 = str(row.iloc[2])
        print(f"[{mpd}] -> Int1: {int1} | Int2: {int2}")
        print("  Parsed Int1:", parse_intervals(int1))
        print("  Parsed Int2:", parse_intervals(int2))
