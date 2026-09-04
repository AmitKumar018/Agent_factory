```python
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, constr
from tools import document_retrieval_tool, pattern_retrieval_tool, ToolException
import logging

app = FastAPI()
logger = logging.getLogger(__name__)

class DocumentQueryParams(BaseModel):
    query: constr(min_length=1, max_length=100)
    project_id: constr(min_length=1, max_length=50)

class PatternQueryParams(BaseModel):
    query: constr(min_length=1, max_length=100)

@app.get("/documents/")
async def get_documents(params: DocumentQueryParams):
    """
    Endpoint to retrieve documents for a given project and query.

    :param params: The query parameters including search query and project ID.
    :return: A list of document metadata.
    """
    try:
        documents = document_retrieval_tool(params.query, params.project_id)
        return {"documents": documents}
    except ToolException as e:
        logger.error(f"Document retrieval failed: {e}")
        raise HTTPException(status_code=500, detail="An error occurred while retrieving documents.")

@app.get("/patterns/")
async def get_patterns(params: PatternQueryParams):
    """
    Endpoint to retrieve patterns based on a query.

    :param params: The query parameters including search query.
    :return: A list of pattern metadata.
    """
    try:
        patterns = pattern_retrieval_tool(params.query)
        return {"patterns": patterns}
    except ToolException as e:
        logger.error(f"Pattern retrieval failed: {e}")
        raise HTTPException(status_code=500, detail="An error occurred while retrieving patterns.")
```