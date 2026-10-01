# NoSQL paths

The MongoDB and Elasticsearch workloads come in two families against the
same data: through the Django integrations, and through the native clients
(`-native-` in the name). They are separate results.

| Family | Asynchronous frameworks | Synchronous frameworks |
| --- | --- | --- |
| `mongo-*` | django-mongodb-backend: async queryset iteration and `acreate`, which run the synchronous driver in a thread | Synchronous querysets |
| `mongo-native-*` | PyMongo `AsyncMongoClient` | PyMongo `MongoClient` |
| `elasticsearch-*` | django-elasticsearch-dsl `Document` operations, in a thread | `Document` operations |
| `elasticsearch-native-*` | `AsyncSearch` and `async_bulk` | `Search` and `bulk` |

Both families use the same collection and index, query, projection, order,
limit of twenty results, aggregation and written document; the responses are
identical, including integer types (PyMongo returns BSON integer subclasses,
which the native path converts). The integration may send the server a
different command: django-mongodb-backend compiles a queryset into an
aggregation pipeline where the native read uses `find()`. A difference between
the families therefore includes the ORM's query compilation, not only a
thread switch.

Searches use Elasticsearch's request cache. Writes add one document per
request with `refresh=False`; the runner refreshes the write index after the
sample to count the documents. The Elasticsearch client is pinned to 9.0.x
([infrastructure](infrastructure.md#elasticsearch)).

A shorter run of these workloads only:

```sh
.venv/bin/aiodrf-bench run --reset-database --workers 1 4 \
  --scenarios mongo-read mongo-aggregate mongo-write \
    mongo-native-read mongo-native-aggregate mongo-native-write \
    elasticsearch-search elasticsearch-aggregate elasticsearch-write \
    elasticsearch-native-search elasticsearch-native-aggregate elasticsearch-native-write \
  --server-cpus 0,2,4,6 --load-cpus 8-15 --output results/nosql
```

`tools/profile_nosql.py` profiles one of these paths in-process, without
HTTP ([profiling](profiling.md)).
