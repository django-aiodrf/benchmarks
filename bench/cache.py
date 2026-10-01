"""Keep django-valkey's async pool owned by one worker lifespan."""

from django_valkey.async_cache.pool import AsyncConnectionFactory


class WorkerAsyncConnectionFactory(AsyncConnectionFactory):
    def get_or_create_connection_pool(self, params):
        # The vendor client already retains this pool. Its default URL-only,
        # process-global pool registry mixes sync/async pools and event loops.
        return self.get_connection_pool(params)
