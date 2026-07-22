from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_standard_web_dashboard_and_assets_are_served():
    page = client.get("/")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "机器人产品情报工作台" in page.text
    assert 'id="view-products"' in page.text
    assert 'id="view-companies"' in page.text
    assert 'id="view-relations"' in page.text
    assert 'id="view-runs"' in page.text
    assert 'id="view-settings"' in page.text

    stylesheet = client.get("/assets/styles.css")
    script = client.get("/assets/app.js")
    assert stylesheet.status_code == 200
    assert script.status_code == 200
    assert ".sidebar" in stylesheet.text
    assert 'api("/runs/current")' in script.text
    assert 'api("/stats")' in script.text


def test_api_routes_remain_available_next_to_web_dashboard():
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/stats").status_code == 200
    assert client.get("/products?limit=1").status_code == 200
    assert client.get("/outputs").status_code == 200
