import pytest
import base64
from fastapi.testclient import TestClient
from core.api.server import app
from core.api.dependencies import get_db

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c

def test_custom_data_backup_restore_endpoints(client):
    """
    Test the backend APIs used by BackupModal.tsx and BackupRestoreModal.tsx
    to ensure description, custom images, and tags are correctly persisted.
    """
    # 1. Setup synthetic mod_id for local download placeholder
    # First we need a local download to exist so the API can generate a placeholder mod
    # For testing, we'll manually insert a dummy local_download directly into the db
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO local_downloads (id, name, path, contents, active_paks) VALUES (9999, 'Test Local Mod', '/tmp/test', '[]', '[]') ON CONFLICT DO NOTHING")
    conn.commit()
    conn.close()

    mod_id = -9999

    # 2. Update custom description (Simulating restore of description)
    test_desc = "This is a custom backup description"
    response = client.patch(f"/api/mods/{mod_id}", json={"description": test_desc})
    assert response.status_code == 200

    # 3. Upload custom images (Simulating restore of custom images)
    dummy_image_data = base64.b64encode(b"dummy_image_content").decode("utf-8")
    payload_images = {
        "images": [
            {
                "data": dummy_image_data,
                "filename": "backup_test.png",
                "mimeType": "image/png"
            }
        ]
    }
    response = client.post(f"/api/mods/{mod_id}/images", json=payload_images)
    assert response.status_code == 200
    assert response.json()["uploaded_count"] == 1

    # 4. Add custom tags (Simulating restore of tags)
    response = client.post(f"/api/mods/{mod_id}/custom-tags", json={"tag": "backup-test-tag"})
    assert response.status_code in (200, 201)

    # 5. Verify the data is saved correctly (Simulating backup snapshot creation)
    
    # Verify description
    response = client.get(f"/api/mods/{mod_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["mod"]["description_bbcode"] == test_desc

    # Verify custom images
    response = client.get(f"/api/mods/{mod_id}/images")
    assert response.status_code == 200
    images = response.json()["custom_images"]
    assert len(images) > 0
    assert images[-1]["filename"] == "backup_test.png"
    assert images[-1]["data"] == dummy_image_data

    # Verify tags
    response = client.get(f"/api/mods/{mod_id}/custom-tags")
    assert response.status_code == 200
    tags = response.json()
    assert any(t["tag"] == "backup-test-tag" for t in tags)

    print("✅ All custom backup and restore endpoints tested successfully!")

