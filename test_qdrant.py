#!/usr/bin/env python3
"""Check what docnames are in Qdrant."""
from pathlib import Path
from qdrant_client import QdrantClient

library_path = Path(__file__).parent / "library"
db_path = library_path / ".qa_vectordb"

print(f"Checking Qdrant at: {db_path}")

# Use synchronous client for this test
client = QdrantClient(path=str(db_path))

try:
    # Check collection
    collection_info = client.get_collection("research_papers")
    print(f"Collection info: {collection_info}")
    print(f"Points count: {collection_info.points_count if hasattr(collection_info, 'points_count') else 'unknown'}")
    
    # Try to peek at some points
    points = client.scroll("research_papers", limit=5)
    print(f"\nFound {len(points[0])} sample points")
    
    for point in points[0]:
        if hasattr(point, 'payload'):
            doc_id = point.payload.get('doc_id', point.payload.get('name', 'unknown'))
            print(f"  - {doc_id}")
        
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
finally:
    client.close()
