from src.civitai import CivitaiPrivateScraper
import json

scraper = CivitaiPrivateScraper(auto_authenticate=True)
data = scraper.scrape(12176069)

if data:
    first_item = data[0]
    print('Keys in first item:')
    for key in sorted(first_item.keys()):
        val = first_item[key]
        if isinstance(val, str):
            print(f'  {key}: {len(val)} chars')
        elif isinstance(val, list):
            print(f'  {key}: list with {len(val)} items')
        elif isinstance(val, dict):
            print(f'  {key}: dict with {len(val)} keys')
        else:
            print(f'  {key}: {type(val).__name__}')

    print()
    print('Full first item:')
    print(json.dumps(first_item, indent=2)[:2000])
else:
    print("No data")
