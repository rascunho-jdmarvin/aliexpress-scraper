import asyncio
from app.db.supabase import db

async def main():
    try:
        res = db.client.table("clients").select("*").limit(1).execute()
        print("Clients:")
        print(res.data)
        
        res = db.client.table("import_jobs").select("*").limit(1).execute()
        print("\nImport Jobs:")
        print(res.data)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
