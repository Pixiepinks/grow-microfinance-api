import os
from supabase import create_client, Client

_supabase_client: Client | None = None

def get_supabase_client() -> Client:
    global _supabase_client
    if _supabase_client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        _supabase_client = create_client(url, key)
    return _supabase_client

def get_storage_bucket() -> str:
    return os.environ.get("SUPABASE_BUCKET", "grow-documents")

def get_upload_prefix() -> str:
    return os.environ.get("SUPABASE_UPLOAD_FOLDER", "loan_documents")

def build_public_url(object_path: str) -> str:
    base = os.environ["SUPABASE_URL"].rstrip("/")
    bucket = get_storage_bucket()
    object_path = object_path.lstrip("/")
    return f"{base}/storage/v1/object/public/{bucket}/{object_path}"
