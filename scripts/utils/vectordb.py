"""
Database health and async utilities for Qdrant vector store.
"""
from qdrant_client import AsyncQdrantClient
import asyncio
import nest_asyncio
from typing import Any, Coroutine


async def validate_qdrant_collection(client: AsyncQdrantClient, collection_name: str) -> bool:
    """
    Validate Qdrant collection is healthy and usable.
    
    Returns:
        bool: True if collection is healthy, False otherwise
    """
    try:
        collection_info = await client.get_collection(collection_name)
        
        # Check if collection has vectors
        if collection_info.vectors_count == 0:
            return False
            
        # Check if collection status is ready
        if hasattr(collection_info, 'status') and collection_info.status != "green":
            return False
            
        return True
    except Exception:
        return False


def safe_async_run(coro: Coroutine) -> Any:
    """
    Safely run async coroutine in sync context with proper event loop handling.
    
    Handles both cases:
    - No event loop: Creates new one
    - Existing running loop: Uses nest_asyncio to allow nesting
    
    Args:
        coro: Async coroutine to execute
        
    Returns:
        Result of coroutine execution
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Nested event loop - use nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(coro)
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        # No event loop - create new one
        return asyncio.run(coro)
