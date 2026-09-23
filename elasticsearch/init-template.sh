#!/bin/sh
# Wait for Elasticsearch to be ready, then create a plain index template
# (no data_stream block, so ES creates regular indices instead of data streams)

until curl -s "http://elasticsearch:9200/_cluster/health" | grep -q '"status"'; do
  echo "Waiting for Elasticsearch..."
  sleep 2
done

echo "Elasticsearch is ready. Creating index template..."

curl -s -X PUT "http://elasticsearch:9200/_index_template/store-logs" \
  -H "Content-Type: application/json" \
  -d '{
    "index_patterns": ["store-logs-*"],
    "priority": 200,
    "template": {
      "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0
      }
    }
  }'

echo ""
echo "Index template created."
