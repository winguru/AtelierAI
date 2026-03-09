from src.civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)
data = scraper.scrape(12176069)

if data:
    print(f"Total items: {len(data)}\n")
    for i in range(min(3, len(data))):
        print(f"Item {i+1}:")
        print(f"  Image ID: {data[i].get('image_id')}")
        print(f"  Model: {data[i].get('model')}")
        pos_prompt = data[i].get('positive_prompt', '')
        neg_prompt = data[i].get('negative_prompt', '')
        print(f"  Positive prompt length: {len(pos_prompt)}")
        print(f"  Positive prompt preview: {pos_prompt[:200] if pos_prompt else 'EMPTY'}")
        print(f"  Negative prompt length: {len(neg_prompt)}")
        print(f"  Negative prompt preview: {neg_prompt[:200] if neg_prompt else 'EMPTY'}")
        print()
else:
    print("No data found")
