import sys
from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-03-33b2ff20a265"

def seed_database():
    db = firestore.Client(project=PROJECT_ID)
    
    routines = [
        {
            "routine_id": "full_body_strength",
            "name": "Full Body Strength Catalyst",
            "category": "strength",
            "difficulty": "intermediate",
            "duration_minutes": 45,
            "target_muscles": ["chest", "back", "legs", "core"],
            "description": "A comprehensive compound strength workout designed for total body muscle activation.",
            "exercises": [
                {"name": "Barbell Squat", "sets": 4, "reps": 10},
                {"name": "Bench Press", "sets": 4, "reps": 8},
                {"name": "Bent-over Row", "sets": 3, "reps": 10},
                {"name": "Plank Hold", "sets": 3, "reps": 60}
            ]
        },
        {
            "routine_id": "hiit_cardio_blast",
            "name": "High-Intensity Cardio Blast",
            "category": "cardio",
            "difficulty": "beginner",
            "duration_minutes": 25,
            "target_muscles": ["legs", "cardiovascular", "core"],
            "description": "Fast-paced interval routine to maximize calorie burn and endurance.",
            "exercises": [
                {"name": "Jumping Jacks", "sets": 4, "reps": 45},
                {"name": "Mountain Climbers", "sets": 4, "reps": 30},
                {"name": "Burpees", "sets": 3, "reps": 15},
                {"name": "High Knees", "sets": 4, "reps": 45}
            ]
        },
        {
            "routine_id": "core_and_mobility",
            "name": "Core Stability & Mobility Flow",
            "category": "mobility",
            "difficulty": "beginner",
            "duration_minutes": 30,
            "target_muscles": ["abs", "lower back", "hips"],
            "description": "Joint mobility and core strengthening routine for injury prevention and recovery.",
            "exercises": [
                {"name": "Bird-Dog", "sets": 3, "reps": 12},
                {"name": "Cat-Cow Stretch", "sets": 3, "reps": 10},
                {"name": "Deadbug", "sets": 3, "reps": 15},
                {"name": "Hip Openers", "sets": 3, "reps": 10}
            ]
        }
    ]
    
    print(f"Seeding Firestore collection 'workout_routines' in project {PROJECT_ID}...")
    for item in routines:
        doc_ref = db.collection("workout_routines").document(item["routine_id"])
        doc_ref.set(item)
        print(f"  ✓ Added routine: {item['name']} ({item['routine_id']})")
        
    print("Database seeding completed successfully!")

if __name__ == "__main__":
    seed_database()
