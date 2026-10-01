"""The DRF authentication and pagination classes of the DRF and adrf adapters."""

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.pagination import PageNumberPagination

from bench.auth import bearer, user
from bench.profiles import AUTH_PAGE_SIZE


class JWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
        value = bearer(request.headers.get("Authorization", ""))
        if value is None:
            return None
        found = user(value)
        if found is None:
            raise AuthenticationFailed("Invalid token.")
        return found, value

    def authenticate_header(self, request):
        return "Bearer"


class ArticlePagination(PageNumberPagination):
    page_size = AUTH_PAGE_SIZE
