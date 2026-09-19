#!/usr/bin/env python3
"""Create or remove clearly labelled BikeBetter sample listings and reports."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from traffic import load_env_file
from firebase_server import FirestoreRepository

DEMO_OWNER = 'bikebetter-demo-owner-v1'
RENTALS = [
    ('annex-hybrid', 'Annex City Hybrid', 'Hybrid', 'Blue', 'Medium', 'The Annex', 800, 2800,
     'An upright city bike with a rear rack and easy gearing. Suits a relaxed ride around campus.'),
    ('kensington-cruiser', 'Kensington Cruiser', 'City', 'Cream', 'Small', 'Kensington Market', 600, 2200,
     'A comfortable step-through frame, front basket and wide saddle for short neighbourhood trips.'),
    ('bellwoods-road', 'Bellwoods Road Bike', 'Road', 'Red', 'Large', 'Trinity Bellwoods', 1200, 4000,
     'A lightweight road-style bike with drop bars for a longer ride around the city.'),
    ('harbourfront-electric', 'Harbourfront Electric', 'Electric', 'Black', 'Medium', 'Harbourfront', 1800, 6000,
     'An upright electric city bike with a rear rack. An example of an assisted-bike rental.'),
    ('riverdale-trail', 'Riverdale Trail Bike', 'Mountain', 'Green', 'Medium', 'Riverdale', 1000, 3500,
     'A sturdy hardtail with wider tyres and flat pedals for a casual weekend outing.'),
    ('campus-commuter', 'Campus Commuter', 'Hybrid', 'Silver', 'Small', 'University of Toronto', 500, 1800,
     'A simple everyday bike with mudguards and lights for trips between classes.'),
]
MISSING = [
    ('blue-commuter', 'Blue City Commuter', 'Hybrid', 'Blue', 'The Annex', 'Near Bloor Street and Spadina Avenue', 10000,
     'Example identifying details: silver rear rack, tan grips and a small yellow bell.'),
    ('red-road', 'Red Road Bike', 'Road', 'Red', 'Kensington Market', 'Near College Street and Augusta Avenue', 15000,
     'Example identifying details: black drop bars, white saddle and a bottle cage.'),
    ('green-step-through', 'Green Step-through', 'City', 'Green', 'Trinity Bellwoods', 'Near Queen Street West and Strachan Avenue', 7500,
     'Example identifying details: front basket, cream tyres and a brown saddle.'),
]


def records():
    now = datetime.now(timezone.utc)
    def bike(model, kind, colour, size, area, hourly=0, daily=0, published=False, description=''):
        return dict(ownerUid=DEMO_OWNER, brand='Demo', model=model, type=kind, colour=colour,
                    frameSize=size, neighbourhood=area, description=description,
                    rateHourCents=hourly, rateDayCents=daily, published=published,
                    available=published, activeRequestId='', createdAt=now, updatedAt=now)
    result = {}
    for slug, model, kind, colour, size, area, hourly, daily, description in RENTALS:
        result['bikes/demo-rental-'+slug] = bike(model, kind, colour, size, area, hourly, daily, True,
            'DEMO LISTING — fictional bike for presentation only; not available to book. '+description)
    for slug, title, kind, colour, area, location, reward, description in MISSING:
        bike_id = 'demo-missing-'+slug
        result['bikes/'+bike_id] = bike(title, kind, colour, 'Medium', area,
            description='DEMO RECORD — fictional bike linked to a sample missing-bike report.')
        result['theftReports/demo-report-'+slug] = dict(
            bikeId=bike_id, ownerUid=DEMO_OWNER, title='Demo · '+title, colour=colour, type=kind,
            neighbourhood=area, lastSeenLocation=location,
            description='DEMO REPORT — fictional scenario, not an actual theft. No reward is payable. '+description,
            contactEmail='demo-tips@example.invalid', rewardCents=reward, status='missing', photoDataUrl='',
            createdAt=now, updatedAt=now)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--apply', action='store_true', help='Create missing demo records; preserve existing ones.')
    choice.add_argument('--remove', action='store_true', help='Delete only these exact records after checking their demo ownership.')
    args = parser.parse_args()
    data = records()
    if not args.apply and not args.remove:
        print('Preview: 6 rental listings, 3 private sample bikes, 3 public missing-bike reports.')
        print('\n'.join(data))
        print('Use --apply to create, or --remove to clean up. No writes made.')
        return
    load_env_file(ROOT / '.env')
    db = FirestoreRepository().db
    batch = db.batch()
    count = 0
    for path, value in data.items():
        ref = db.document(path)
        snapshot = ref.get()
        if snapshot.exists:
            saved = snapshot.to_dict()
            if saved.get('ownerUid') != DEMO_OWNER or saved.get('activeRequestId'):
                raise RuntimeError('Refusing to modify a non-demo or reserved record: '+path)
            if args.remove:
                batch.delete(ref, option=db.write_option(last_update_time=snapshot.update_time))
                count += 1
        elif args.apply:
            batch.create(ref, value)
            count += 1
    if count:
        batch.commit()
    expected_exists = not args.remove
    assert all(db.document(path).get().exists == expected_exists for path in data)
    print(f'{"Created" if args.apply else "Removed"} {count} demo documents in {db.project}. Verified all expected records.')


if __name__ == '__main__':
    main()
