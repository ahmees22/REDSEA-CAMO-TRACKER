import json

try:
    with open("excel_struct.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    with open("headers.txt", "w", encoding="utf-8") as out:
        for sheet in ["MAIN", "1C TASK LIST", "OOP TASK LIST", "AD CN TASK LIST", "GEN", "HOURS AND CYCLES", "B008-RSA OCCM", "MAIN mpd 6-2025"]:
            if sheet in data["data"]:
                out.write(f"--- {sheet} ---\n")
                out.write(f"Columns:\n{data['data'][sheet]['columns']}\n")
                if data['data'][sheet]['sample']:
                    out.write(f"Sample 0:\n{data['data'][sheet]['sample'][0]}\n")
                    if len(data['data'][sheet]['sample']) > 1:
                        out.write(f"Sample 1:\n{data['data'][sheet]['sample'][1]}\n")
                out.write("\n")
    print("Done generating headers.txt")
except Exception as e:
    print(f"Error: {e}")
