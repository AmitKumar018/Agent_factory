```python
from typing import List, Dict, Any
from retrieval import retrieve_documents, retrieve_patterns, RetrievalException
from langgraph.prebuilt import ToolNode

class ToolException(Exception):
    """Custom exception for tool errors."""
    pass

def document_retrieval_tool(query: str, project_id: str) -> List[Dict[str, Any]]:
    """
    Tool to retrieve documents for a given project and query.

    :param query: The search query.
    :param project_id: The ID of the project.
    :return: A list of document metadata.
    :raises ToolException: If retrieval fails.
    """
    if not query or not project_id:
        raise ToolException("Query and project_id must be provided.")
    
    try:
        documents = retrieve_documents(query, project_id)
        return [{"id": doc.id, "content": doc.content, "relevance": doc.relevance_score} for doc in documents]
    except RetrievalException as e:
        raise ToolException(f"Document retrieval failed: {e}")

def pattern_retrieval_tool(query: str) -> List[Dict[str, Any]]:
    """
    Tool to retrieve patterns based on a query.

    :param query: The search query.
    :return: A list of pattern metadata.
    :raises ToolException: If retrieval fails.
    """
    if not query:
        raise ToolException("Query must be provided.")
    
    try:
        patterns = retrieve_patterns(query)
        return patterns
    except RetrievalException as e:
        raise ToolException(f"Pattern retrieval failed: {e}")

# Define the tools registry
tools = [
    ToolNode(name="document_retrieval_tool", tool=document_retrieval_tool),
    ToolNode(name="pattern_retrieval_tool", tool=pattern_retrieval_tool)
]
```