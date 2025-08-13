import sqlalchemy
import json
import os
import re

# --- Configuration ---
DATABASE_URL = 'sqlite:///amarshashtho.db'
JSON_FILE_PATH = 'BD Doctor_Search.json'
SYNONYMS_FILE_PATH = 'specialty_synonyms.json'

# --- SQLAlchemy Setup ---
engine = sqlalchemy.create_engine(DATABASE_URL)
metadata = sqlalchemy.MetaData()

doctors_table = sqlalchemy.Table('doctors', metadata,
    sqlalchemy.Column('id', sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column('name', sqlalchemy.String, nullable=False),
    sqlalchemy.Column('primary_specialty', sqlalchemy.String, nullable=False),
    sqlalchemy.Column('specialties', sqlalchemy.Text),
    sqlalchemy.Column('location_text', sqlalchemy.String),
    sqlalchemy.Column('phone', sqlalchemy.String, nullable=True),
    sqlalchemy.Column('email', sqlalchemy.String, nullable=True),
    sqlalchemy.Column('clinic_address', sqlalchemy.Text),
    sqlalchemy.Column('profile_image', sqlalchemy.String),
    sqlalchemy.Column('rating', sqlalchemy.Float, nullable=True),
    sqlalchemy.Column('notes', sqlalchemy.Text)
)

def clean_text(text):
    if not text or not isinstance(text, str): return ""
    return re.sub(r'\s+', ' ', text).strip()

def extract_location(info_string):
    if not info_string: return "Unknown"
    match = re.search(r"Working\s+Area:\s*([\w\s-]+?),\s*([\w\s\d.-]+)", info_string, re.IGNORECASE)
    if match:
        city, area = clean_text(match.group(1)), clean_text(match.group(2))
        return f"{city}, {area}" if area else city
    return "Unknown"

def create_reverse_synonym_map(synonyms_path):
    """Creates a map from any synonym to its canonical name."""
    with open(synonyms_path, 'r', encoding='utf-8') as f:
        synonyms_data = json.load(f)
    
    reverse_map = {}
    for canonical_name, synonym_list in synonyms_data.items():
        for synonym in synonym_list:
            reverse_map[synonym.strip().title()] = canonical_name
    return reverse_map

def main():
    print("--- AmarShashtho Doctor Database Initializer (v2) ---")
    
    if not os.path.exists(JSON_FILE_PATH) or not os.path.exists(SYNONYMS_FILE_PATH):
        print(f"Error: Make sure both '{JSON_FILE_PATH}' and '{SYNONYMS_FILE_PATH}' exist.")
        return

    # 1. Create the reverse map for standardization
    print("Loading specialty synonyms...")
    reverse_synonyms = create_reverse_synonym_map(SYNONYMS_FILE_PATH)

    # 2. Create table
    metadata.create_all(engine)
    print("'doctors' table created or already exists.")

    # 3. Read doctor data
    with open(JSON_FILE_PATH, 'r', encoding='utf-8') as f:
        doctor_data_list = json.load(f)
    print(f"Loaded {len(doctor_data_list)} doctor records from JSON.")

    # 4. Process and prepare data for insertion
    doctors_to_insert = []
    for doc in doctor_data_list:
        raw_specialty = clean_text(doc.get('mb2', 'Others')).title()
        
        # --- FIX: Standardize the specialty using our map ---
        canonical_specialty = reverse_synonyms.get(raw_specialty, "Others")
        
        record = {
            'name': clean_text(doc.get('Title', 'No Name Provided')),
            'primary_specialty': canonical_specialty,
            'specialties': json.dumps([canonical_specialty]),
            'location_text': extract_location(doc.get('Info', '')),
            'clinic_address': f"{clean_text(doc.get('mb0', ''))} {clean_text(doc.get('mb02', ''))}".strip(),
            'profile_image': doc.get('Image', ''),
            'notes': f"Qualifications: {clean_text(doc.get('aonmedteamdiscription', ''))}\nProfile URL: {doc.get('Title_URL', '')}".strip(),
            'phone': None, 'email': None, 'rating': None
        }
        doctors_to_insert.append(record)

    # 5. Insert data into the database
    if doctors_to_insert:
        with engine.connect() as connection:
            with connection.begin() as transaction:
                try:
                    print("Clearing existing data from 'doctors' table...")
                    connection.execute(doctors_table.delete())
                    print(f"Inserting {len(doctors_to_insert)} standardized records...")
                    connection.execute(doctors_table.insert(), doctors_to_insert)
                    transaction.commit()
                    print("\n--- Success! ---")
                    print(f"Database has been successfully populated with clean data.")
                except Exception as e:
                    print(f"Database error: {e}"); transaction.rollback()

if __name__ == '__main__':
    main()