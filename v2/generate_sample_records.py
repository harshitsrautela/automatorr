"""Generates sample_records.json with random valid test data (usage: python generate_sample_records.py [count])."""

import json
import random
import sys

COUNTRIES = [
    "Afghanistan", "Albania", "Algeria", "Argentina", "Armenia", "Australia",
    "Austria", "Azerbaijan", "Bahrain", "Bangladesh", "Belarus", "Belgium",
    "Bolivia", "Bosnia and Herzegovina", "Brazil", "Bulgaria", "Cambodia",
    "Cameroon", "Canada", "Chile", "China", "Colombia", "Costa Rica", "Croatia",
    "Cuba", "Cyprus", "Czech Republic", "Denmark", "Dominican Republic",
    "Ecuador", "Egypt", "El Salvador", "Estonia", "Ethiopia", "Finland",
    "France", "Georgia", "Germany", "Ghana", "Greece", "Guatemala", "Honduras",
    "Hong Kong", "Hungary", "Iceland", "India", "Indonesia", "Iran", "Iraq",
    "Ireland", "Israel", "Italy", "Ivory Coast", "Jamaica", "Japan", "Jordan",
    "Kazakhstan", "Kenya", "Kuwait", "Latvia", "Lebanon", "Lithuania",
    "Luxembourg", "Malaysia", "Malta", "Mexico", "Moldova", "Monaco",
    "Mongolia", "Montenegro", "Morocco", "Myanmar", "Nepal", "Netherlands",
    "New Zealand", "Nigeria", "North Macedonia", "Norway", "Oman", "Pakistan",
    "Panama", "Paraguay", "Peru", "Philippines", "Poland", "Portugal", "Qatar",
    "Romania", "Russia", "Rwanda", "Saudi Arabia", "Senegal", "Serbia",
    "Singapore", "Slovakia", "Slovenia", "South Africa", "South Korea",
    "Spain", "Sri Lanka", "Sweden", "Switzerland", "Taiwan", "Tanzania",
    "Thailand", "Tunisia", "Turkey", "Uganda", "Ukraine", "United Arab Emirates",
    "United Kingdom", "United States", "Uruguay", "Uzbekistan", "Venezuela",
    "Vietnam", "Zambia", "Zimbabwe",
]

CITIES_BY_COUNTRY = {
    "India": ["Bengaluru", "Mumbai", "Delhi", "Hyderabad"],
    "United States": ["Austin", "Seattle", "Chicago", "Denver"],
    "United Kingdom": ["London", "Manchester", "Bristol"],
    "France": ["Lyon", "Paris", "Marseille"],
    "Germany": ["Berlin", "Munich", "Hamburg"],
    "Japan": ["Tokyo", "Osaka", "Yokohama"],
    "South Korea": ["Seoul", "Busan", "Incheon"],
    "Canada": ["Toronto", "Vancouver", "Montreal"],
    "Australia": ["Sydney", "Melbourne", "Brisbane"],
    "Mexico": ["Guadalajara", "Mexico City", "Monterrey"],
    "Singapore": ["Singapore"],
    "Brazil": ["Sao Paulo", "Rio de Janeiro", "Brasilia"],
    "Spain": ["Madrid", "Barcelona", "Valencia"],
    "Italy": ["Rome", "Milan", "Naples"],
    "Netherlands": ["Amsterdam", "Rotterdam", "Utrecht"],
    "Ireland": ["Dublin", "Cork"],
    "Sweden": ["Stockholm", "Gothenburg"],
    "Poland": ["Warsaw", "Krakow"],
    "South Africa": ["Cape Town", "Johannesburg"],
    "Nigeria": ["Lagos", "Abuja"],
    "Kenya": ["Nairobi", "Mombasa"],
    "Egypt": ["Cairo", "Alexandria"],
    "United Arab Emirates": ["Dubai", "Abu Dhabi"],
    "Philippines": ["Manila", "Cebu City"],
    "Indonesia": ["Jakarta", "Surabaya"],
    "Vietnam": ["Ho Chi Minh City", "Hanoi"],
    "Turkey": ["Istanbul", "Ankara"],
    "Argentina": ["Buenos Aires", "Cordoba"],
    "Chile": ["Santiago", "Valparaiso"],
    "Colombia": ["Bogota", "Medellin"],
    "New Zealand": ["Auckland", "Wellington"],
    "Switzerland": ["Zurich", "Geneva"],
    "Portugal": ["Lisbon", "Porto"],
    "Denmark": ["Copenhagen", "Aarhus"],
    "Norway": ["Oslo", "Bergen"],
    "Finland": ["Helsinki", "Tampere"],
    "Israel": ["Tel Aviv", "Jerusalem"],
    "Saudi Arabia": ["Riyadh", "Jeddah"],
}
_DEFAULT_CITY = "Central District"

DEPARTMENTS = [
    "Engineering", "Product", "Design", "Marketing", "Sales", "Finance",
    "Human Resources", "Data", "Research", "Security", "Infrastructure",
    "Architecture", "Operations",
]

EMPLOYMENT_TYPES = ["Full Time", "Part Time", "Contract", "Intern", "Freelance"]

GENDERS = ["Male", "Female", "Non-binary", "Prefer not to say"]

JOB_TITLES = [
    "Software Engineer", "Solutions Architect", "Sales Lead",
    "Security Engineer", "Product Manager", "Research Scientist",
    "Marketing Specialist", "QA Engineer", "Financial Analyst",
    "Data Scientist", "DevOps Engineer", "UX Designer",
    "Customer Success Manager", "Recruiter", "Business Analyst",
    "Technical Writer", "Account Executive", "Operations Manager",
]

COMPANIES = [
    "Cobalt Dynamics", "Meridian Foods", "Solara Systems", "Nordwind Labs",
    "Vantage Retail Co", "Harborlight Group", "Ferrovia Analytics",
    "Brightfield Energy", "Kestrel Biotech", "Lumen Software",
    "Granite Peak Logistics", "Oakshade Media", "Pinecrest Financial",
    "Tidewater Robotics", "Amberline Health",
]

FIRST_NAMES = [
    "Thiago", "Lucia", "Sebastian", "Wren", "Bjorn", "Aarav", "Anya", "Kenji",
    "Sofia", "Ingrid", "Mateo", "Priya", "Noah", "Elena", "Omar", "Freya",
    "Diego", "Amara", "Lars", "Yuki", "Isabela", "Kwame", "Nadia", "Finn",
    "Camila", "Tariq", "Saoirse", "Haruto", "Zara", "Mikael", "Leilani",
    "Dmitri", "Amina", "Rowan", "Ines", "Kian", "Mei", "Oluwaseun", "Greta",
    "Arjun",
]

LAST_NAMES = [
    "Moreau", "Mensah", "Nakamura", "Sorensen", "Ferreira", "Haddad",
    "Okafor", "Choi", "Fischer", "Castillo", "Larsen", "Kowalski",
    "Abernathy", "Petrov", "Nguyen", "Osei", "Hartley", "Dubois", "Santos",
    "Lindqvist", "Hassan", "Ricci", "Barros", "Kallas", "Whitfield",
    "Yilmaz", "Kobayashi", "Novak", "Mbeki", "Sheridan",
]

NOTES_POOL = [
    "Prefers async communication.",
    "Available for on-call rotation.",
    "Relocating next quarter.",
    "Former intern, converted to full-time.",
    "",
    "",
    "Open to relocation for the right role.",
    "Needs visa sponsorship renewal next year.",
    "Mentoring two junior hires this quarter.",
    "Returning from parental leave in June.",
]

STREET_NAMES = [
    "2nd Ave", "Main St", "Oak Ave", "Elm Court", "Highland Rd",
    "Birchwood Ln", "Sunset Blvd", "River Rd", "Maple St", "Cedar Ave",
]


# Builds a random testmail.dev email for a name that is not already in the used set.
# Takes first, last and the used set, records the new email in it, and returns it.
def _unique_email(first, last, used):
    base = f"{first.lower()}.{last.lower()}"
    while True:
        suffix = f"{random.randint(100, 999)}{random.choice('abcdefghijklmnopqrstuvwxyz')}{random.randint(10, 99)}"
        candidate = f"{base}.{suffix}@testmail.dev"
        if candidate not in used:
            used.add(candidate)
            return candidate


# Generates random valid person records with unique emails.
# Takes the record count and returns a list of record dicts.
def generate_records(count: int) -> list[dict]:
    used_emails: set[str] = set()
    records = []
    for i in range(1, count + 1):
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        country = random.choice(COUNTRIES)
        city = random.choice(CITIES_BY_COUNTRY.get(country, [_DEFAULT_CITY]))
        birth_year = random.randint(1960, 2003)
        birth_month = random.randint(1, 12)
        birth_day = random.randint(1, 28)

        records.append({
            "record_id": str(i),
            "first_name": first,
            "last_name": last,
            "email": _unique_email(first, last, used_emails),
            "phone": f"+1-555-{random.randint(10000, 99999)}",
            "date_of_birth": f"{birth_year:04d}-{birth_month:02d}-{birth_day:02d}",
            "gender": random.choice(GENDERS),
            "company": random.choice(COMPANIES),
            "job_title": random.choice(JOB_TITLES),
            "country": country,
            "city": city,
            "address": f"{random.randint(100, 9999)} {random.choice(STREET_NAMES)}",
            "postal_code": str(random.randint(10000, 99999)),
            "department": random.choice(DEPARTMENTS),
            "employment_type": random.choice(EMPLOYMENT_TYPES),
            "experience_years": random.randint(0, 30),
            "salary": random.randint(45000, 180000),
            "newsletter": random.choice([True, False]),
            "notes": random.choice(NOTES_POOL),
        })
    return records


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    records = generate_records(count)
    with open("sample_records.json", "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)
    print(f"Wrote {len(records)} records to sample_records.json")
