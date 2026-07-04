from fastapi.testclient import TestClient
import main

client = TestClient(main.app)

def test_api_orders_no_longer_raises_keyerror_in_bug_mode():
    """
    Regression test for the KeyError in total_revenue.

    This test activates the 'bug' fault mode, which previously caused a
    KeyError when accessing the /api/orders endpoint. It asserts that the
    endpoint now returns a 200 OK status and the correct revenue payload,
    verifying that the fix is effective.
    """
    # Arrange: Set the application to the 'bug' fault mode that caused the error.
    fault_response = client.post("/__fault/bug")
    assert fault_response.status_code == 200
    assert fault_response.json() == {"fault": "bug"}

    # Act: Call the endpoint that was previously failing.
    response = client.get("/api/orders")

    # Assert: Verify the endpoint is now successful and returns the correct data.
    assert response.status_code == 200
    data = response.json()
    assert "orders" in data
    assert "revenue" in data
    # The revenue should be the sum of prices from the ORDERS constant (10 + 25).
    assert data["revenue"] == 35

    # Teardown: Reset the fault mode to 'off'.
    client.post("/__fault/off")
