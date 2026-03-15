import pandas as pd

def parse_sheet(file_path, sheet_name):
    # Read without header
    df = pd.read_excel(file_path, sheet_name=sheet_name, engine='pyxlsb', header=None)
    
    header_idx = -1
    for idx, row in df.head(10).iterrows():
        str_row = [str(x).upper().strip() for x in row.values]
        if 'LAST DONE' in str_row or 'TASK NO.' in str_row or 'NEXT DUE' in str_row or 'DESCRIPTION' in str_row or 'TITLE' in str_row:
            header_idx = idx
            break
            
    if header_idx == -1:
        print(f"[{sheet_name}] Header not found!")
        return
        
    h1 = [str(x).replace('\n', ' ').strip() if pd.notna(x) else "" for x in df.iloc[header_idx].values]
    
    # Ensure there's a next row
    if header_idx + 1 < len(df):
        h2 = [str(x).replace('\n', ' ').strip() if pd.notna(x) else "" for x in df.iloc[header_idx+1].values]
    else:
        h2 = [""] * len(h1)
    
    final_cols = []
    last_h1 = ""
    for c1, c2 in zip(h1, h2):
        if c1 and c1.lower() != 'nan' and 'unnamed' not in c1.lower():
            last_h1 = c1
        else:
            c1 = last_h1 
            
        c2_clean = c2 if c2.lower() != 'nan' and 'unnamed' not in c2.lower() else ''
        
        col_name = f"{c1} {c2_clean}".strip()
        final_cols.append(col_name)
            
    df.columns = final_cols
    df = df.iloc[header_idx+2:].dropna(how='all').reset_index(drop=True)
    
    print(f"\n{'='*10} [{sheet_name}] {'='*10}")
    print("Parsed Columns:", final_cols)
    print("First Valid Task Row:")
    
    # Print the first row that actually has a task ID or Description
    for _, row in df.iterrows():
        # check if it's not totally empty in string columns
        vals = [str(v) for v in row.values if pd.notna(v) and str(v).strip() != 'nan']
        if len(vals) > 2:
            print(row.to_dict())
            break

try:
    parse_sheet('SU-RSA MASTER.xlsb', '1C TASK LIST')
    parse_sheet('SU-RSA MASTER.xlsb', 'OOP TASK LIST')
except Exception as e:
    print(f"Error: {e}")
