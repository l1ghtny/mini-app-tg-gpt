import uuid

from app.db.models import ChatFolder, ChatFolderDocument
from app.schemas.chat_folders import ChatFolder as ChatFolderResponse


def test_project_document_ids_are_exposed_in_folder_response():
    document_id = uuid.uuid4()
    folder = ChatFolder(user_id=uuid.uuid4(), name="Research")
    folder.attached_documents = [
        ChatFolderDocument(folder_id=folder.id, document_id=document_id),
    ]

    response = ChatFolderResponse.model_validate(folder)

    assert response.document_ids == [document_id]
