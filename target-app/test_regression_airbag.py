from fastapi.testclient import TestClient

import main

client = TestClient(main.app)

def test_orders_api_bug_mode_is_fixed():
    """
    Verify that the KeyError in total_revenue is fixed.

    This test activates the 'bug' fault mode, which previously caused a
    KeyError when accessing the /api/orders endpoint. It asserts that the
    endpoint now returns a 200 OK status and the correct data, proving
    the bug has been resolved.
    """
    # Set fault mode to 'bug' to simulate the condition that caused the error
    response_fault = client.post("/__fault/bug")
    assert response_fault.status_code == 200
    assert response_fault.json() == {"fault": "bug"}

    # Call the endpoint that was previously failing
    response_orders = client.get("/api/orders")

    # Assert that the endpoint is now resilient to the 'bug' mode
    assert response_orders.status_code == 200
    data = response_orders.json()
    assert "orders" in data
    assert "revenue" in data
    # Check if revenue is calculated correctly (10 + 25)
    assert data["revenue"] == 35

    # Cleanup: Reset fault mode to 'off'
    client.post("/__fault/off")
