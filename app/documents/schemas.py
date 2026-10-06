
from datetime import datetime
from typing import Optional
from pydantic import BaseModel
from app.documents.models import ParseStatus, DocumentKind



class DocumentUploadResponse(BaseModel):
    """
    Returned immediately after a file is uploaded (before parsing is done).
    The user should poll GET /documents/{id} until status = ready.
    """
    document_id: str
    filename: str
    status: ParseStatus
    message: str   # human-readable hint like "Parse job started"



# Document status (polling response)                                   


class DocumentStatusResponse(BaseModel):
    """
    Full document details — returned by GET /projects/{id}/documents/{doc_id}.
    The user polls this until status flips from 'parsing' to 'ready' or 'failed'.
    """
    id: str
    project_id: str
    filename: str
    extension: str
    mime_type: str
    file_size: int
    kind: DocumentKind
    status: ParseStatus
    section_count: int
    chunk_count: int
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}



# Document list                                                         

class DocumentListResponse(BaseModel):
    """Returned by GET /projects/{id}/documents"""
    items: list[DocumentStatusResponse]
    total: int



# Section outline                                                       
 

class SectionItem(BaseModel):
    """One entry in the document's section outline."""
    section_id: str
    section_title: str
    chunk_index: int
    page: int
    text_preview: str   


class DocumentSectionsResponse(BaseModel):
    """
    Returned by GET /projects/{id}/documents/{doc_id}/sections.
    Shows the structured outline of the parsed document.
    """
    document_id: str
    filename: str
    sections: list[SectionItem]
    total_sections: int
