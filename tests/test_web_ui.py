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
    assert 'name="max_api_calls"' in page.text
    assert 'name="airia_key"' in page.text
    assert 'name="app_key"' in page.text
    assert 'name="secret_key"' in page.text
    assert 'name="tavily_api_key"' in page.text
    assert 'name="bing_api_key"' in page.text
    assert 'app.js?v=20260724-airia8' in page.text
    assert 'name="qcc_test_keyword"' in page.text
    assert 'id="testQccApi"' in page.text

    stylesheet = client.get("/assets/styles.css")
    script = client.get("/assets/app.js")
    assert stylesheet.status_code == 200
    assert script.status_code == 200
    assert ".sidebar" in stylesheet.text
    assert 'api("/runs/current")' in script.text
    assert 'api("/stats")' in script.text
    assert 'api("/qcc/test"' in script.text
    assert 'api("/qcc-config"' in script.text
    assert "工商候选诊断（最近 20 条）" in script.text
    render_run = script.text.split("function renderRun(run)", 1)[1].split(
        "function renderAnalysis", 1
    )[0]
    assert render_run.index("const qccDiagnosticsHtml") < render_run.index(
        "${qccDiagnosticsHtml}"
    )


def test_api_routes_remain_available_next_to_web_dashboard():
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/stats").status_code == 200
    assert client.get("/products?limit=1").status_code == 200
    assert client.get("/outputs").status_code == 200
