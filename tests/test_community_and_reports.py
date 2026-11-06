import asyncio
from starlette.testclient import TestClient
from app.main import app
from app.database import init_db
from app.services.admin_auth import get_stored_fallback_password

def test_full_community_and_reports_lifecycle():
    asyncio.run(init_db())
    client = TestClient(app)

    # 1. Submit a public report (site technical issue)
    rep_res = client.post("/api/reports", json={
        "category": "site_issue",
        "title": "مشكلة في تشغيل سيرفر الأنمي",
        "details": "السيرفر لا يعمل عند اختيار الجودة العالية",
        "contact_email": "user@example.com",
        "visitor_id": "test_visitor_123"
    })
    assert rep_res.status_code == 200, rep_res.text
    rep_data = rep_res.json()
    assert rep_data["status"] == "success"
    report_id = rep_data["report_id"]
    assert report_id > 0

    # 2. Submit a broken episode report
    rep_res2 = client.post("/api/reports", json={
        "category": "broken_episode",
        "title": "حلقة ناقصة",
        "details": "الحلقة العاشرة غير متوفرة في السيرفر",
        "anime_id": 55,
        "episode_number": 10,
        "visitor_id": "test_visitor_456"
    })
    assert rep_res2.status_code == 200
    report_id2 = rep_res2.json()["report_id"]

    # 3. Admin login
    admin_pass = get_stored_fallback_password()
    login_res = client.post("/api/admin/login", json={
        "username": "admin",
        "password": admin_pass
    })
    assert login_res.status_code == 200, login_res.text

    # 4. List reports as admin
    admin_rep_res = client.get("/api/admin/reports")
    assert admin_rep_res.status_code == 200
    reports = admin_rep_res.json()["items"]
    assert any(r["id"] == report_id for r in reports)
    assert any(r["id"] == report_id2 for r in reports)

    # 5. Update report status
    patch_res = client.patch(f"/api/admin/reports/{report_id}", json={
        "status": "in_progress",
        "admin_notes": "جاري التحقق من السيرفر"
    })
    assert patch_res.status_code == 200
    assert patch_res.json()["new_status"] == "in_progress"

    # 6. Episode rating test
    import time
    test_media_id = int(time.time()) % 1000000 + 1000

    rate_res = client.post(f"/api/ratings/{test_media_id}/1", json={
        "score": 5,
        "visitor_id": "visitor_alpha"
    })
    assert rate_res.status_code == 200
    rate_data = rate_res.json()
    assert rate_data["total_votes"] == 1
    assert rate_data["average_score"] == 5.0

    # Another visitor votes 3 stars
    rate_res2 = client.post(f"/api/ratings/{test_media_id}/1", json={
        "score": 3,
        "visitor_id": "visitor_beta"
    })
    assert rate_res2.status_code == 200
    assert rate_res2.json()["average_score"] == 4.0

    # Query ratings
    get_rate = client.get(f"/api/ratings/{test_media_id}/1?visitor_id=visitor_alpha")
    assert get_rate.status_code == 200
    rate_query = get_rate.json()
    assert rate_query["user_score"] == 5

    # 7. Episode comments test
    comm_res = client.post(f"/api/comments/{test_media_id}/1", json={
        "content": "حلقة أسطورية ومؤثرة جداً!",
        "author_name": "أحمد المصري",
        "visitor_id": "visitor_alpha"
    })
    assert comm_res.status_code == 200
    comm_data = comm_res.json()["comment"]
    comment_id = comm_data["id"]
    assert comm_data["author_name"] == "أحمد المصري"

    # Fetch comments
    list_comm = client.get(f"/api/comments/{test_media_id}/1?visitor_id=visitor_beta")
    assert list_comm.status_code == 200
    comments_list = list_comm.json()["items"]
    assert any(c["id"] == comment_id for c in comments_list)

    # Like comment
    like_res = client.post(f"/api/comments/{comment_id}/like?visitor_id=visitor_beta")
    print("Like response:", like_res.status_code, like_res.text)
    assert like_res.status_code == 200, like_res.text
    assert like_res.json()["likes_count"] == 1

    # Admin comments list
    admin_comm_res = client.get("/api/admin/comments")
    assert admin_comm_res.status_code == 200
    admin_comms = admin_comm_res.json()["items"]
    assert any(c["id"] == comment_id for c in admin_comms)

    # Admin toggle approval
    toggle_res = client.patch(f"/api/admin/comments/{comment_id}/toggle-approval")
    assert toggle_res.status_code == 200
    assert toggle_res.json()["is_approved"] is False

    # Admin delete comment
    del_comm = client.delete(f"/api/admin/comments/{comment_id}")
    assert del_comm.status_code == 200

    # Admin delete report
    del_rep = client.delete(f"/api/admin/reports/{report_id}")
    assert del_rep.status_code == 200
    del_rep2 = client.delete(f"/api/admin/reports/{report_id2}")
    assert del_rep2.status_code == 200

    print("ALL COMMUNITY, RATING, COMMENTS, AND REPORTS TESTS PASSED COMPLETELY!")

if __name__ == "__main__":
    test_full_community_and_reports_lifecycle()
