#template script to ensure base URL returns status 200

import os
import requests


BASE_URL = os.environ["dev.nafn.us"].rstrip("/")


def test_application_is_reachable():
    response = requests.get(
        f"{BASE_URL}/",
        timeout=10
    )
    assert response.status_code == 200


# template function that can test specific endpoints
# def test_home_endpoint():
#    response = requests.get(
#        f"{BASE_URL}/home",
#        timeout=10,
#    ) 

#    assert response.status_code == 200
#    assert response.json()["status"] == "ok"
