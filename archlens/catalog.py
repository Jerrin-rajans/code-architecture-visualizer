"""Detection catalog: maps observable facts onto canonical service keys.

A canonical key is ``<provider>.<service>`` (``aws.lambda``, ``onprem.postgresql``).
The icon registry in :mod:`archlens.render.icons` resolves those keys to concrete
``diagrams`` node classes, so this file and that one must agree on spelling.

Every rule carries a `ComponentKind` because the kind drives both the layout tier
and the fallback icon when a provider-specific icon is unavailable.
"""

from __future__ import annotations

import re

from .models import ComponentKind as K

# A rule is (canonical_service, human_label, kind).
Rule = tuple[str, str, K]


# --------------------------------------------------------------------------- #
# Package dependencies -> services
#
# Keys are matched against the *normalised* package name (lowercased, with `_`
# and `.` folded to `-`). Longest key wins, so `azure-storage-blob` beats
# `azure-storage`.
# --------------------------------------------------------------------------- #

DEPENDENCY_RULES: dict[str, Rule] = {
    # -- AWS ---------------------------------------------------------------
    "boto3": ("aws.sdk", "AWS SDK", K.INFRA),
    "botocore": ("aws.sdk", "AWS SDK", K.INFRA),
    "aws-sdk": ("aws.sdk", "AWS SDK", K.INFRA),
    "@aws-sdk/client-s3": ("aws.s3", "Amazon S3", K.STORAGE),
    "@aws-sdk/client-dynamodb": ("aws.dynamodb", "DynamoDB", K.DATABASE),
    "@aws-sdk/client-sqs": ("aws.sqs", "Amazon SQS", K.QUEUE),
    "@aws-sdk/client-sns": ("aws.sns", "Amazon SNS", K.QUEUE),
    "@aws-sdk/client-lambda": ("aws.lambda", "AWS Lambda", K.FUNCTION),
    "@aws-sdk/client-kinesis": ("aws.kinesis", "Kinesis", K.STREAM),
    "@aws-sdk/client-secrets-manager": ("aws.secretsmanager", "Secrets Manager", K.AUTH),
    "@aws-sdk/client-cognito-identity-provider": ("aws.cognito", "Cognito", K.AUTH),
    "aws-lambda-powertools": ("aws.lambda", "AWS Lambda", K.FUNCTION),
    "chalice": ("aws.lambda", "AWS Lambda", K.FUNCTION),
    "aws-cdk-lib": ("aws.cloudformation", "AWS CDK", K.CICD),
    "serverless": ("aws.lambda", "AWS Lambda", K.FUNCTION),
    "amazon-dax-client": ("aws.dynamodb", "DynamoDB", K.DATABASE),
    "aws-amplify": ("aws.amplify", "AWS Amplify", K.FRONTEND),
    "sagemaker": ("aws.sagemaker", "SageMaker", K.ML),
    # -- Azure -------------------------------------------------------------
    "azure-storage-blob": ("azure.blobstorage", "Blob Storage", K.STORAGE),
    "azure-storage-queue": ("azure.queuestorage", "Queue Storage", K.QUEUE),
    "azure-storage-file-datalake": ("azure.datalake", "Data Lake Storage", K.STORAGE),
    "azure-cosmos": ("azure.cosmosdb", "Cosmos DB", K.DATABASE),
    "azure-servicebus": ("azure.servicebus", "Service Bus", K.QUEUE),
    "azure-eventhub": ("azure.eventhubs", "Event Hubs", K.STREAM),
    "azure-eventgrid": ("azure.eventgrid", "Event Grid", K.QUEUE),
    "azure-keyvault-secrets": ("azure.keyvault", "Key Vault", K.AUTH),
    "azure-keyvault": ("azure.keyvault", "Key Vault", K.AUTH),
    "azure-identity": ("azure.activedirectory", "Entra ID", K.AUTH),
    "azure-functions": ("azure.functions", "Azure Functions", K.FUNCTION),
    "azure-ai-ml": ("azure.machinelearning", "Azure ML", K.ML),
    "azure-search-documents": ("azure.search", "AI Search", K.SEARCH),
    "@azure/storage-blob": ("azure.blobstorage", "Blob Storage", K.STORAGE),
    "@azure/service-bus": ("azure.servicebus", "Service Bus", K.QUEUE),
    "@azure/cosmos": ("azure.cosmosdb", "Cosmos DB", K.DATABASE),
    "@azure/identity": ("azure.activedirectory", "Entra ID", K.AUTH),
    "applicationinsights": ("azure.appinsights", "App Insights", K.MONITORING),
    "opencensus-ext-azure": ("azure.appinsights", "App Insights", K.MONITORING),
    # -- GCP ---------------------------------------------------------------
    "google-cloud-storage": ("gcp.gcs", "Cloud Storage", K.STORAGE),
    "google-cloud-pubsub": ("gcp.pubsub", "Pub/Sub", K.QUEUE),
    "google-cloud-bigquery": ("gcp.bigquery", "BigQuery", K.ANALYTICS),
    "google-cloud-firestore": ("gcp.firestore", "Firestore", K.DATABASE),
    "google-cloud-datastore": ("gcp.datastore", "Datastore", K.DATABASE),
    "google-cloud-spanner": ("gcp.spanner", "Cloud Spanner", K.DATABASE),
    "google-cloud-bigtable": ("gcp.bigtable", "Bigtable", K.DATABASE),
    "google-cloud-tasks": ("gcp.tasks", "Cloud Tasks", K.QUEUE),
    "google-cloud-run": ("gcp.run", "Cloud Run", K.SERVICE),
    "google-cloud-functions": ("gcp.functions", "Cloud Functions", K.FUNCTION),
    "google-cloud-aiplatform": ("gcp.aiplatform", "Vertex AI", K.ML),
    "google-cloud-secret-manager": ("gcp.kms", "Secret Manager", K.AUTH),
    "firebase-admin": ("gcp.firestore", "Firebase", K.DATABASE),
    "@google-cloud/storage": ("gcp.gcs", "Cloud Storage", K.STORAGE),
    "@google-cloud/pubsub": ("gcp.pubsub", "Pub/Sub", K.QUEUE),
    "@google-cloud/bigquery": ("gcp.bigquery", "BigQuery", K.ANALYTICS),
    # -- Databases ---------------------------------------------------------
    "psycopg2": ("onprem.postgresql", "PostgreSQL", K.DATABASE),
    "psycopg2-binary": ("onprem.postgresql", "PostgreSQL", K.DATABASE),
    "psycopg": ("onprem.postgresql", "PostgreSQL", K.DATABASE),
    "asyncpg": ("onprem.postgresql", "PostgreSQL", K.DATABASE),
    "pg": ("onprem.postgresql", "PostgreSQL", K.DATABASE),
    "pg-promise": ("onprem.postgresql", "PostgreSQL", K.DATABASE),
    "postgres": ("onprem.postgresql", "PostgreSQL", K.DATABASE),
    "mysqlclient": ("onprem.mysql", "MySQL", K.DATABASE),
    "pymysql": ("onprem.mysql", "MySQL", K.DATABASE),
    "mysql2": ("onprem.mysql", "MySQL", K.DATABASE),
    "mysql-connector-python": ("onprem.mysql", "MySQL", K.DATABASE),
    "mariadb": ("onprem.mariadb", "MariaDB", K.DATABASE),
    "pymongo": ("onprem.mongodb", "MongoDB", K.DATABASE),
    "motor": ("onprem.mongodb", "MongoDB", K.DATABASE),
    "mongoose": ("onprem.mongodb", "MongoDB", K.DATABASE),
    "mongodb": ("onprem.mongodb", "MongoDB", K.DATABASE),
    "cassandra-driver": ("onprem.cassandra", "Cassandra", K.DATABASE),
    "pyodbc": ("onprem.mssql", "SQL Server", K.DATABASE),
    "pymssql": ("onprem.mssql", "SQL Server", K.DATABASE),
    "mssql": ("onprem.mssql", "SQL Server", K.DATABASE),
    "tedious": ("onprem.mssql", "SQL Server", K.DATABASE),
    "cx-oracle": ("onprem.oracle", "Oracle DB", K.DATABASE),
    "oracledb": ("onprem.oracle", "Oracle DB", K.DATABASE),
    "neo4j": ("onprem.neo4j", "Neo4j", K.DATABASE),
    "influxdb-client": ("onprem.influxdb", "InfluxDB", K.DATABASE),
    "clickhouse-driver": ("onprem.clickhouse", "ClickHouse", K.DATABASE),
    "clickhouse-connect": ("onprem.clickhouse", "ClickHouse", K.DATABASE),
    "couchdb": ("onprem.couchdb", "CouchDB", K.DATABASE),
    "sqlalchemy": ("onprem.sql", "SQL (SQLAlchemy)", K.DATABASE),
    "prisma": ("onprem.sql", "SQL (Prisma)", K.DATABASE),
    "typeorm": ("onprem.sql", "SQL (TypeORM)", K.DATABASE),
    "sequelize": ("onprem.sql", "SQL (Sequelize)", K.DATABASE),
    "knex": ("onprem.sql", "SQL (Knex)", K.DATABASE),
    "drizzle-orm": ("onprem.sql", "SQL (Drizzle)", K.DATABASE),
    "alembic": ("onprem.sql", "SQL migrations", K.DATABASE),
    # -- Cache / in-memory --------------------------------------------------
    "redis": ("onprem.redis", "Redis", K.CACHE),
    "ioredis": ("onprem.redis", "Redis", K.CACHE),
    "redis-py": ("onprem.redis", "Redis", K.CACHE),
    "aioredis": ("onprem.redis", "Redis", K.CACHE),
    "pymemcache": ("onprem.memcached", "Memcached", K.CACHE),
    "memcached": ("onprem.memcached", "Memcached", K.CACHE),
    "hazelcast-python-client": ("onprem.hazelcast", "Hazelcast", K.CACHE),
    # -- Queues / streaming -------------------------------------------------
    "kafka-python": ("onprem.kafka", "Apache Kafka", K.STREAM),
    "confluent-kafka": ("onprem.kafka", "Apache Kafka", K.STREAM),
    "aiokafka": ("onprem.kafka", "Apache Kafka", K.STREAM),
    "kafkajs": ("onprem.kafka", "Apache Kafka", K.STREAM),
    "pika": ("onprem.rabbitmq", "RabbitMQ", K.QUEUE),
    "aio-pika": ("onprem.rabbitmq", "RabbitMQ", K.QUEUE),
    "amqplib": ("onprem.rabbitmq", "RabbitMQ", K.QUEUE),
    "kombu": ("onprem.rabbitmq", "RabbitMQ", K.QUEUE),
    "celery": ("onprem.celery", "Celery", K.WORKER),
    "rq": ("onprem.celery", "RQ workers", K.WORKER),
    "bullmq": ("onprem.celery", "BullMQ", K.WORKER),
    "bull": ("onprem.celery", "Bull", K.WORKER),
    "nats-py": ("onprem.nats", "NATS", K.QUEUE),
    "stomp-py": ("onprem.activemq", "ActiveMQ", K.QUEUE),
    "pyzmq": ("onprem.zeromq", "ZeroMQ", K.QUEUE),
    # -- Search -------------------------------------------------------------
    "elasticsearch": ("onprem.elasticsearch", "Elasticsearch", K.SEARCH),
    "opensearch-py": ("onprem.elasticsearch", "OpenSearch", K.SEARCH),
    "@elastic/elasticsearch": ("onprem.elasticsearch", "Elasticsearch", K.SEARCH),
    "pysolr": ("onprem.solr", "Apache Solr", K.SEARCH),
    "meilisearch": ("onprem.elasticsearch", "Meilisearch", K.SEARCH),
    "algoliasearch": ("saas.algolia", "Algolia", K.SEARCH),
    # -- Vector stores / AI --------------------------------------------------
    "pinecone-client": ("generic.vectordb", "Pinecone", K.DATABASE),
    "pinecone": ("generic.vectordb", "Pinecone", K.DATABASE),
    "weaviate-client": ("generic.vectordb", "Weaviate", K.DATABASE),
    "qdrant-client": ("generic.vectordb", "Qdrant", K.DATABASE),
    "chromadb": ("generic.vectordb", "Chroma", K.DATABASE),
    "faiss-cpu": ("generic.vectordb", "FAISS", K.DATABASE),
    "anthropic": ("saas.anthropic", "Claude API", K.EXTERNAL),
    "openai": ("saas.openai", "OpenAI API", K.EXTERNAL),
    "langchain": ("generic.llmframework", "LangChain", K.ML),
    "llama-index": ("generic.llmframework", "LlamaIndex", K.ML),
    "transformers": ("generic.llmframework", "Transformers", K.ML),
    "torch": ("generic.ml", "PyTorch", K.ML),
    "tensorflow": ("generic.ml", "TensorFlow", K.ML),
    "scikit-learn": ("generic.ml", "scikit-learn", K.ML),
    "mlflow": ("onprem.mlflow", "MLflow", K.ML),
    # -- Web frameworks / runtimes -------------------------------------------
    "fastapi": ("programming.fastapi", "FastAPI", K.SERVICE),
    "flask": ("programming.flask", "Flask", K.SERVICE),
    "django": ("programming.django", "Django", K.SERVICE),
    "starlette": ("programming.fastapi", "Starlette", K.SERVICE),
    "tornado": ("programming.python", "Tornado", K.SERVICE),
    "aiohttp": ("programming.python", "aiohttp", K.SERVICE),
    "express": ("programming.nodejs", "Express", K.SERVICE),
    "koa": ("programming.nodejs", "Koa", K.SERVICE),
    "fastify": ("programming.nodejs", "Fastify", K.SERVICE),
    "nestjs": ("programming.nodejs", "NestJS", K.SERVICE),
    "@nestjs/core": ("programming.nodejs", "NestJS", K.SERVICE),
    "next": ("programming.react", "Next.js", K.FRONTEND),
    "nuxt": ("programming.vue", "Nuxt", K.FRONTEND),
    "react": ("programming.react", "React", K.FRONTEND),
    "react-dom": ("programming.react", "React", K.FRONTEND),
    "vue": ("programming.vue", "Vue", K.FRONTEND),
    "@angular/core": ("programming.angular", "Angular", K.FRONTEND),
    "svelte": ("programming.svelte", "Svelte", K.FRONTEND),
    "spring-boot-starter-web": ("programming.spring", "Spring Boot", K.SERVICE),
    "spring-boot": ("programming.spring", "Spring Boot", K.SERVICE),
    "rails": ("programming.rails", "Ruby on Rails", K.SERVICE),
    "laravel": ("programming.laravel", "Laravel", K.SERVICE),
    "gin-gonic": ("programming.go", "Gin", K.SERVICE),
    "graphql": ("programming.graphql", "GraphQL", K.GATEWAY),
    "apollo-server": ("programming.graphql", "Apollo GraphQL", K.GATEWAY),
    "strawberry-graphql": ("programming.graphql", "GraphQL", K.GATEWAY),
    "grpcio": ("generic.grpc", "gRPC", K.SERVICE),
    "@grpc/grpc-js": ("generic.grpc", "gRPC", K.SERVICE),
    "uvicorn": ("onprem.nginx", "Uvicorn (ASGI)", K.INFRA),
    "gunicorn": ("onprem.nginx", "Gunicorn (WSGI)", K.INFRA),
    # -- Auth / identity -----------------------------------------------------
    "auth0": ("saas.auth0", "Auth0", K.AUTH),
    "pyjwt": ("generic.jwt", "JWT auth", K.AUTH),
    "jsonwebtoken": ("generic.jwt", "JWT auth", K.AUTH),
    "passport": ("generic.jwt", "Passport auth", K.AUTH),
    "python-keycloak": ("onprem.keycloak", "Keycloak", K.AUTH),
    "okta-jwt-verifier": ("saas.okta", "Okta", K.AUTH),
    "authlib": ("generic.jwt", "OAuth2 / OIDC", K.AUTH),
    "hvac": ("onprem.vault", "HashiCorp Vault", K.AUTH),
    # -- Observability -------------------------------------------------------
    "prometheus-client": ("onprem.prometheus", "Prometheus", K.MONITORING),
    "prom-client": ("onprem.prometheus", "Prometheus", K.MONITORING),
    "opentelemetry-sdk": ("generic.otel", "OpenTelemetry", K.MONITORING),
    "opentelemetry-api": ("generic.otel", "OpenTelemetry", K.MONITORING),
    "@opentelemetry/sdk-node": ("generic.otel", "OpenTelemetry", K.MONITORING),
    "sentry-sdk": ("onprem.sentry", "Sentry", K.MONITORING),
    "@sentry/node": ("onprem.sentry", "Sentry", K.MONITORING),
    "datadog": ("saas.datadog", "Datadog", K.MONITORING),
    "ddtrace": ("saas.datadog", "Datadog APM", K.MONITORING),
    "newrelic": ("onprem.newrelic", "New Relic", K.MONITORING),
    "structlog": ("onprem.fluentd", "Structured logging", K.MONITORING),
    # -- Third-party SaaS ----------------------------------------------------
    "stripe": ("saas.stripe", "Stripe", K.EXTERNAL),
    "twilio": ("saas.twilio", "Twilio", K.EXTERNAL),
    "sendgrid": ("saas.sendgrid", "SendGrid", K.EXTERNAL),
    "slack-sdk": ("saas.slack", "Slack", K.EXTERNAL),
    "@slack/web-api": ("saas.slack", "Slack", K.EXTERNAL),
    "snowflake-connector-python": ("saas.snowflake", "Snowflake", K.ANALYTICS),
    "cloudinary": ("saas.cloudinary", "Cloudinary", K.STORAGE),
    "requests": ("generic.http", "HTTP client", K.INFRA),
    "httpx": ("generic.http", "HTTP client", K.INFRA),
    "axios": ("generic.http", "HTTP client", K.INFRA),
    # -- Data / workflow -----------------------------------------------------
    "apache-airflow": ("onprem.airflow", "Apache Airflow", K.WORKER),
    "prefect": ("onprem.airflow", "Prefect", K.WORKER),
    "dagster": ("onprem.airflow", "Dagster", K.WORKER),
    "pyspark": ("onprem.spark", "Apache Spark", K.ANALYTICS),
    "dbt-core": ("onprem.dbt", "dbt", K.ANALYTICS),
    "pandas": ("generic.dataframe", "pandas", K.ANALYTICS),
    "apache-beam": ("onprem.beam", "Apache Beam", K.ANALYTICS),
}

# Dependencies that are pure noise on an architecture diagram.
DEPENDENCY_IGNORE = {
    "pytest", "mypy", "ruff", "black", "flake8", "isort", "tox", "coverage",
    "setuptools", "wheel", "pip", "typing-extensions", "pydantic", "attrs",
    "eslint", "prettier", "jest", "vitest", "typescript", "webpack", "vite",
    "babel", "tslib", "rimraf", "nodemon", "ts-node", "husky", "lint-staged",
}


# --------------------------------------------------------------------------- #
# Import statements -> services
#
# Matched as a prefix against the dotted/slashed module path, so
# `azure.storage.blob.aio` still resolves via `azure.storage.blob`.
# --------------------------------------------------------------------------- #

IMPORT_RULES: dict[str, Rule] = {
    "boto3": ("aws.sdk", "AWS SDK", K.INFRA),
    "azure.storage.blob": ("azure.blobstorage", "Blob Storage", K.STORAGE),
    "azure.cosmos": ("azure.cosmosdb", "Cosmos DB", K.DATABASE),
    "azure.servicebus": ("azure.servicebus", "Service Bus", K.QUEUE),
    "azure.eventhub": ("azure.eventhubs", "Event Hubs", K.STREAM),
    "azure.keyvault": ("azure.keyvault", "Key Vault", K.AUTH),
    "azure.identity": ("azure.activedirectory", "Entra ID", K.AUTH),
    "azure.functions": ("azure.functions", "Azure Functions", K.FUNCTION),
    "google.cloud.storage": ("gcp.gcs", "Cloud Storage", K.STORAGE),
    "google.cloud.pubsub": ("gcp.pubsub", "Pub/Sub", K.QUEUE),
    "google.cloud.bigquery": ("gcp.bigquery", "BigQuery", K.ANALYTICS),
    "google.cloud.firestore": ("gcp.firestore", "Firestore", K.DATABASE),
    "google.cloud.spanner": ("gcp.spanner", "Cloud Spanner", K.DATABASE),
    "firebase_admin": ("gcp.firestore", "Firebase", K.DATABASE),
    "psycopg2": ("onprem.postgresql", "PostgreSQL", K.DATABASE),
    "psycopg": ("onprem.postgresql", "PostgreSQL", K.DATABASE),
    "asyncpg": ("onprem.postgresql", "PostgreSQL", K.DATABASE),
    "pymongo": ("onprem.mongodb", "MongoDB", K.DATABASE),
    "motor": ("onprem.mongodb", "MongoDB", K.DATABASE),
    "redis": ("onprem.redis", "Redis", K.CACHE),
    "kafka": ("onprem.kafka", "Apache Kafka", K.STREAM),
    "confluent_kafka": ("onprem.kafka", "Apache Kafka", K.STREAM),
    "pika": ("onprem.rabbitmq", "RabbitMQ", K.QUEUE),
    "celery": ("onprem.celery", "Celery", K.WORKER),
    "elasticsearch": ("onprem.elasticsearch", "Elasticsearch", K.SEARCH),
    "sqlalchemy": ("onprem.sql", "SQL (SQLAlchemy)", K.DATABASE),
    "fastapi": ("programming.fastapi", "FastAPI", K.SERVICE),
    "flask": ("programming.flask", "Flask", K.SERVICE),
    "django": ("programming.django", "Django", K.SERVICE),
    "anthropic": ("saas.anthropic", "Claude API", K.EXTERNAL),
    "openai": ("saas.openai", "OpenAI API", K.EXTERNAL),
    "langchain": ("generic.llmframework", "LangChain", K.ML),
    "stripe": ("saas.stripe", "Stripe", K.EXTERNAL),
    "twilio": ("saas.twilio", "Twilio", K.EXTERNAL),
    "prometheus_client": ("onprem.prometheus", "Prometheus", K.MONITORING),
    "opentelemetry": ("generic.otel", "OpenTelemetry", K.MONITORING),
    "sentry_sdk": ("onprem.sentry", "Sentry", K.MONITORING),
    "airflow": ("onprem.airflow", "Apache Airflow", K.WORKER),
    "pyspark": ("onprem.spark", "Apache Spark", K.ANALYTICS),
    # JS/TS module specifiers use the same table via `/`->`.` folding.
    "aws-sdk": ("aws.sdk", "AWS SDK", K.INFRA),
    "express": ("programming.nodejs", "Express", K.SERVICE),
    "next": ("programming.react", "Next.js", K.FRONTEND),
    "react": ("programming.react", "React", K.FRONTEND),
    "vue": ("programming.vue", "Vue", K.FRONTEND),
    "mongoose": ("onprem.mongodb", "MongoDB", K.DATABASE),
    "ioredis": ("onprem.redis", "Redis", K.CACHE),
    "kafkajs": ("onprem.kafka", "Apache Kafka", K.STREAM),
}


# --------------------------------------------------------------------------- #
# Terraform resource types -> services
#
# Matched longest-prefix, so `aws_lambda_function_url` still maps via
# `aws_lambda_function`.
# --------------------------------------------------------------------------- #

TERRAFORM_RULES: dict[str, Rule] = {
    # AWS
    "aws_lambda_function": ("aws.lambda", "AWS Lambda", K.FUNCTION),
    "aws_ecs_service": ("aws.ecs", "Amazon ECS", K.SERVICE),
    "aws_ecs_cluster": ("aws.ecs", "Amazon ECS", K.SERVICE),
    "aws_eks_cluster": ("aws.eks", "Amazon EKS", K.SERVICE),
    "aws_eks_node_group": ("aws.eks", "EKS node group", K.SERVICE),
    "aws_instance": ("aws.ec2", "Amazon EC2", K.SERVICE),
    "aws_autoscaling_group": ("aws.ec2", "EC2 Auto Scaling", K.SERVICE),
    "aws_launch_template": ("aws.ec2", "EC2 launch template", K.SERVICE),
    "aws_batch_job_definition": ("aws.batch", "AWS Batch", K.WORKER),
    "aws_apprunner_service": ("aws.apprunner", "App Runner", K.SERVICE),
    "aws_elastic_beanstalk": ("aws.beanstalk", "Elastic Beanstalk", K.SERVICE),
    "aws_s3_bucket": ("aws.s3", "Amazon S3", K.STORAGE),
    "aws_efs_file_system": ("aws.efs", "Amazon EFS", K.STORAGE),
    "aws_db_instance": ("aws.rds", "Amazon RDS", K.DATABASE),
    "aws_rds_cluster": ("aws.aurora", "Amazon Aurora", K.DATABASE),
    "aws_dynamodb_table": ("aws.dynamodb", "DynamoDB", K.DATABASE),
    "aws_elasticache_cluster": ("aws.elasticache", "ElastiCache", K.CACHE),
    "aws_elasticache_replication_group": ("aws.elasticache", "ElastiCache", K.CACHE),
    "aws_redshift_cluster": ("aws.redshift", "Redshift", K.ANALYTICS),
    "aws_docdb_cluster": ("aws.documentdb", "DocumentDB", K.DATABASE),
    "aws_neptune_cluster": ("aws.neptune", "Neptune", K.DATABASE),
    "aws_sqs_queue": ("aws.sqs", "Amazon SQS", K.QUEUE),
    "aws_sns_topic": ("aws.sns", "Amazon SNS", K.QUEUE),
    "aws_kinesis_stream": ("aws.kinesis", "Kinesis Data Streams", K.STREAM),
    "aws_kinesis_firehose": ("aws.firehose", "Kinesis Firehose", K.STREAM),
    "aws_msk_cluster": ("aws.msk", "Amazon MSK", K.STREAM),
    "aws_cloudwatch_event_rule": ("aws.eventbridge", "EventBridge", K.QUEUE),
    "aws_sfn_state_machine": ("aws.stepfunctions", "Step Functions", K.WORKER),
    "aws_api_gateway_rest_api": ("aws.apigateway", "API Gateway", K.GATEWAY),
    "aws_apigatewayv2_api": ("aws.apigateway", "API Gateway", K.GATEWAY),
    "aws_lb": ("aws.elb", "Elastic Load Balancer", K.GATEWAY),
    "aws_alb": ("aws.alb", "Application Load Balancer", K.GATEWAY),
    "aws_cloudfront_distribution": ("aws.cloudfront", "CloudFront", K.CDN),
    "aws_route53_zone": ("aws.route53", "Route 53", K.INFRA),
    "aws_route53_record": ("aws.route53", "Route 53", K.INFRA),
    "aws_vpc": ("aws.vpc", "VPC", K.INFRA),
    "aws_subnet": ("aws.subnet", "Subnet", K.INFRA),
    "aws_nat_gateway": ("aws.natgateway", "NAT Gateway", K.INFRA),
    "aws_cognito_user_pool": ("aws.cognito", "Cognito", K.AUTH),
    "aws_secretsmanager_secret": ("aws.secretsmanager", "Secrets Manager", K.AUTH),
    "aws_kms_key": ("aws.kms", "AWS KMS", K.AUTH),
    "aws_wafv2_web_acl": ("aws.waf", "AWS WAF", K.AUTH),
    "aws_iam_role": ("aws.iam", "IAM", K.AUTH),
    "aws_cloudwatch_log_group": ("aws.cloudwatch", "CloudWatch", K.MONITORING),
    "aws_glue_job": ("aws.glue", "AWS Glue", K.ANALYTICS),
    "aws_athena_workgroup": ("aws.athena", "Athena", K.ANALYTICS),
    "aws_emr_cluster": ("aws.emr", "Amazon EMR", K.ANALYTICS),
    "aws_opensearch_domain": ("aws.opensearch", "OpenSearch", K.SEARCH),
    "aws_elasticsearch_domain": ("aws.opensearch", "OpenSearch", K.SEARCH),
    "aws_sagemaker_endpoint": ("aws.sagemaker", "SageMaker", K.ML),
    "aws_ecr_repository": ("aws.ecr", "Amazon ECR", K.CICD),
    # Azure
    "azurerm_linux_function_app": ("azure.functions", "Azure Functions", K.FUNCTION),
    "azurerm_windows_function_app": ("azure.functions", "Azure Functions", K.FUNCTION),
    "azurerm_function_app": ("azure.functions", "Azure Functions", K.FUNCTION),
    "azurerm_linux_web_app": ("azure.appservice", "App Service", K.SERVICE),
    "azurerm_windows_web_app": ("azure.appservice", "App Service", K.SERVICE),
    "azurerm_app_service": ("azure.appservice", "App Service", K.SERVICE),
    "azurerm_service_plan": ("azure.appserviceplan", "App Service Plan", K.INFRA),
    "azurerm_kubernetes_cluster": ("azure.aks", "Azure Kubernetes Service", K.SERVICE),
    "azurerm_container_app": ("azure.containerapps", "Container Apps", K.SERVICE),
    "azurerm_container_group": ("azure.containerinstances", "Container Instances", K.SERVICE),
    "azurerm_container_registry": ("azure.acr", "Container Registry", K.CICD),
    "azurerm_linux_virtual_machine": ("azure.vm", "Virtual Machine", K.SERVICE),
    "azurerm_virtual_machine": ("azure.vm", "Virtual Machine", K.SERVICE),
    "azurerm_storage_account": ("azure.storageaccount", "Storage Account", K.STORAGE),
    "azurerm_storage_container": ("azure.blobstorage", "Blob Storage", K.STORAGE),
    "azurerm_storage_queue": ("azure.queuestorage", "Queue Storage", K.QUEUE),
    "azurerm_mssql_server": ("azure.sqlserver", "Azure SQL Server", K.DATABASE),
    "azurerm_mssql_database": ("azure.sqldatabase", "Azure SQL Database", K.DATABASE),
    "azurerm_sql_database": ("azure.sqldatabase", "Azure SQL Database", K.DATABASE),
    "azurerm_cosmosdb_account": ("azure.cosmosdb", "Cosmos DB", K.DATABASE),
    "azurerm_postgresql_flexible_server": ("azure.postgresql", "Azure DB for PostgreSQL", K.DATABASE),
    "azurerm_postgresql_server": ("azure.postgresql", "Azure DB for PostgreSQL", K.DATABASE),
    "azurerm_mysql_flexible_server": ("azure.mysql", "Azure DB for MySQL", K.DATABASE),
    "azurerm_redis_cache": ("azure.redis", "Azure Cache for Redis", K.CACHE),
    "azurerm_servicebus_namespace": ("azure.servicebus", "Service Bus", K.QUEUE),
    "azurerm_servicebus_queue": ("azure.servicebus", "Service Bus queue", K.QUEUE),
    "azurerm_servicebus_topic": ("azure.servicebus", "Service Bus topic", K.QUEUE),
    "azurerm_eventhub": ("azure.eventhubs", "Event Hubs", K.STREAM),
    "azurerm_eventgrid_topic": ("azure.eventgrid", "Event Grid", K.QUEUE),
    "azurerm_api_management": ("azure.apimanagement", "API Management", K.GATEWAY),
    "azurerm_application_gateway": ("azure.appgateway", "Application Gateway", K.GATEWAY),
    "azurerm_lb": ("azure.loadbalancer", "Load Balancer", K.GATEWAY),
    "azurerm_cdn_profile": ("azure.cdn", "Azure CDN", K.CDN),
    "azurerm_cdn_frontdoor_profile": ("azure.frontdoor", "Front Door", K.CDN),
    "azurerm_frontdoor": ("azure.frontdoor", "Front Door", K.CDN),
    "azurerm_virtual_network": ("azure.vnet", "Virtual Network", K.INFRA),
    "azurerm_subnet": ("azure.subnet", "Subnet", K.INFRA),
    "azurerm_key_vault": ("azure.keyvault", "Key Vault", K.AUTH),
    "azurerm_application_insights": ("azure.appinsights", "Application Insights", K.MONITORING),
    "azurerm_log_analytics_workspace": ("azure.loganalytics", "Log Analytics", K.MONITORING),
    "azurerm_data_factory": ("azure.datafactory", "Data Factory", K.ANALYTICS),
    "azurerm_synapse_workspace": ("azure.synapse", "Synapse Analytics", K.ANALYTICS),
    "azurerm_databricks_workspace": ("azure.databricks", "Azure Databricks", K.ANALYTICS),
    "azurerm_search_service": ("azure.search", "AI Search", K.SEARCH),
    "azurerm_machine_learning_workspace": ("azure.machinelearning", "Azure ML", K.ML),
    # GCP
    "google_cloud_run_service": ("gcp.run", "Cloud Run", K.SERVICE),
    "google_cloud_run_v2_service": ("gcp.run", "Cloud Run", K.SERVICE),
    "google_cloudfunctions_function": ("gcp.functions", "Cloud Functions", K.FUNCTION),
    "google_cloudfunctions2_function": ("gcp.functions", "Cloud Functions", K.FUNCTION),
    "google_container_cluster": ("gcp.gke", "Google Kubernetes Engine", K.SERVICE),
    "google_compute_instance": ("gcp.computeengine", "Compute Engine", K.SERVICE),
    "google_app_engine_application": ("gcp.appengine", "App Engine", K.SERVICE),
    "google_storage_bucket": ("gcp.gcs", "Cloud Storage", K.STORAGE),
    "google_sql_database_instance": ("gcp.sql", "Cloud SQL", K.DATABASE),
    "google_firestore_database": ("gcp.firestore", "Firestore", K.DATABASE),
    "google_bigtable_instance": ("gcp.bigtable", "Bigtable", K.DATABASE),
    "google_spanner_instance": ("gcp.spanner", "Cloud Spanner", K.DATABASE),
    "google_redis_instance": ("gcp.memorystore", "Memorystore", K.CACHE),
    "google_pubsub_topic": ("gcp.pubsub", "Pub/Sub topic", K.QUEUE),
    "google_pubsub_subscription": ("gcp.pubsub", "Pub/Sub subscription", K.QUEUE),
    "google_cloud_tasks_queue": ("gcp.tasks", "Cloud Tasks", K.QUEUE),
    "google_bigquery_dataset": ("gcp.bigquery", "BigQuery", K.ANALYTICS),
    "google_bigquery_table": ("gcp.bigquery", "BigQuery", K.ANALYTICS),
    "google_dataflow_job": ("gcp.dataflow", "Dataflow", K.ANALYTICS),
    "google_compute_global_forwarding_rule": ("gcp.loadbalancing", "Cloud Load Balancing", K.GATEWAY),
    "google_compute_backend_service": ("gcp.loadbalancing", "Cloud Load Balancing", K.GATEWAY),
    "google_api_gateway_api": ("gcp.apigateway", "API Gateway", K.GATEWAY),
    "google_compute_network": ("gcp.vpc", "VPC network", K.INFRA),
    "google_compute_subnetwork": ("gcp.vpc", "Subnetwork", K.INFRA),
    "google_kms_crypto_key": ("gcp.kms", "Cloud KMS", K.AUTH),
    "google_secret_manager_secret": ("gcp.kms", "Secret Manager", K.AUTH),
    # Kubernetes provider
    "kubernetes_deployment": ("k8s.deployment", "Deployment", K.SERVICE),
    "kubernetes_service": ("k8s.service", "Service", K.GATEWAY),
    "kubernetes_ingress": ("k8s.ingress", "Ingress", K.GATEWAY),
    "kubernetes_stateful_set": ("k8s.statefulset", "StatefulSet", K.SERVICE),
    "kubernetes_cron_job": ("k8s.cronjob", "CronJob", K.WORKER),
}


# --------------------------------------------------------------------------- #
# CloudFormation / SAM  ->  services
# --------------------------------------------------------------------------- #

CLOUDFORMATION_RULES: dict[str, Rule] = {
    "AWS::Lambda::Function": ("aws.lambda", "AWS Lambda", K.FUNCTION),
    "AWS::Serverless::Function": ("aws.lambda", "AWS Lambda", K.FUNCTION),
    "AWS::Serverless::Api": ("aws.apigateway", "API Gateway", K.GATEWAY),
    "AWS::ApiGateway::RestApi": ("aws.apigateway", "API Gateway", K.GATEWAY),
    "AWS::ApiGatewayV2::Api": ("aws.apigateway", "API Gateway", K.GATEWAY),
    "AWS::S3::Bucket": ("aws.s3", "Amazon S3", K.STORAGE),
    "AWS::DynamoDB::Table": ("aws.dynamodb", "DynamoDB", K.DATABASE),
    "AWS::Serverless::SimpleTable": ("aws.dynamodb", "DynamoDB", K.DATABASE),
    "AWS::RDS::DBInstance": ("aws.rds", "Amazon RDS", K.DATABASE),
    "AWS::RDS::DBCluster": ("aws.aurora", "Amazon Aurora", K.DATABASE),
    "AWS::SQS::Queue": ("aws.sqs", "Amazon SQS", K.QUEUE),
    "AWS::SNS::Topic": ("aws.sns", "Amazon SNS", K.QUEUE),
    "AWS::Kinesis::Stream": ("aws.kinesis", "Kinesis", K.STREAM),
    "AWS::Events::Rule": ("aws.eventbridge", "EventBridge", K.QUEUE),
    "AWS::StepFunctions::StateMachine": ("aws.stepfunctions", "Step Functions", K.WORKER),
    "AWS::ECS::Service": ("aws.ecs", "Amazon ECS", K.SERVICE),
    "AWS::ECS::Cluster": ("aws.ecs", "Amazon ECS", K.SERVICE),
    "AWS::EKS::Cluster": ("aws.eks", "Amazon EKS", K.SERVICE),
    "AWS::EC2::Instance": ("aws.ec2", "Amazon EC2", K.SERVICE),
    "AWS::EC2::VPC": ("aws.vpc", "VPC", K.INFRA),
    "AWS::EC2::Subnet": ("aws.subnet", "Subnet", K.INFRA),
    "AWS::ElastiCache::CacheCluster": ("aws.elasticache", "ElastiCache", K.CACHE),
    "AWS::ElastiCache::ReplicationGroup": ("aws.elasticache", "ElastiCache", K.CACHE),
    "AWS::CloudFront::Distribution": ("aws.cloudfront", "CloudFront", K.CDN),
    "AWS::ElasticLoadBalancingV2::LoadBalancer": ("aws.alb", "Application Load Balancer", K.GATEWAY),
    "AWS::Cognito::UserPool": ("aws.cognito", "Cognito", K.AUTH),
    "AWS::SecretsManager::Secret": ("aws.secretsmanager", "Secrets Manager", K.AUTH),
    "AWS::Logs::LogGroup": ("aws.cloudwatch", "CloudWatch Logs", K.MONITORING),
    "AWS::Glue::Job": ("aws.glue", "AWS Glue", K.ANALYTICS),
    "AWS::Redshift::Cluster": ("aws.redshift", "Redshift", K.ANALYTICS),
}


# --------------------------------------------------------------------------- #
# Azure ARM / Bicep resource providers -> services
# --------------------------------------------------------------------------- #

ARM_RULES: dict[str, Rule] = {
    "microsoft.web/sites": ("azure.appservice", "App Service", K.SERVICE),
    "microsoft.web/serverfarms": ("azure.appserviceplan", "App Service Plan", K.INFRA),
    "microsoft.app/containerapps": ("azure.containerapps", "Container Apps", K.SERVICE),
    "microsoft.containerservice/managedclusters": ("azure.aks", "AKS", K.SERVICE),
    "microsoft.containerregistry/registries": ("azure.acr", "Container Registry", K.CICD),
    "microsoft.storage/storageaccounts": ("azure.storageaccount", "Storage Account", K.STORAGE),
    "microsoft.sql/servers": ("azure.sqlserver", "Azure SQL Server", K.DATABASE),
    "microsoft.sql/servers/databases": ("azure.sqldatabase", "Azure SQL Database", K.DATABASE),
    "microsoft.documentdb/databaseaccounts": ("azure.cosmosdb", "Cosmos DB", K.DATABASE),
    "microsoft.cache/redis": ("azure.redis", "Azure Cache for Redis", K.CACHE),
    "microsoft.servicebus/namespaces": ("azure.servicebus", "Service Bus", K.QUEUE),
    "microsoft.eventhub/namespaces": ("azure.eventhubs", "Event Hubs", K.STREAM),
    "microsoft.eventgrid/topics": ("azure.eventgrid", "Event Grid", K.QUEUE),
    "microsoft.apimanagement/service": ("azure.apimanagement", "API Management", K.GATEWAY),
    "microsoft.network/applicationgateways": ("azure.appgateway", "Application Gateway", K.GATEWAY),
    "microsoft.network/loadbalancers": ("azure.loadbalancer", "Load Balancer", K.GATEWAY),
    "microsoft.network/frontdoors": ("azure.frontdoor", "Front Door", K.CDN),
    "microsoft.cdn/profiles": ("azure.cdn", "Azure CDN", K.CDN),
    "microsoft.network/virtualnetworks": ("azure.vnet", "Virtual Network", K.INFRA),
    "microsoft.keyvault/vaults": ("azure.keyvault", "Key Vault", K.AUTH),
    "microsoft.insights/components": ("azure.appinsights", "Application Insights", K.MONITORING),
    "microsoft.operationalinsights/workspaces": ("azure.loganalytics", "Log Analytics", K.MONITORING),
    "microsoft.datafactory/factories": ("azure.datafactory", "Data Factory", K.ANALYTICS),
    "microsoft.synapse/workspaces": ("azure.synapse", "Synapse Analytics", K.ANALYTICS),
    "microsoft.search/searchservices": ("azure.search", "AI Search", K.SEARCH),
    "microsoft.cognitiveservices/accounts": ("azure.cognitiveservices", "Azure AI Services", K.ML),
    "microsoft.machinelearningservices/workspaces": ("azure.machinelearning", "Azure ML", K.ML),
}


# --------------------------------------------------------------------------- #
# Kubernetes manifest kinds -> services
# --------------------------------------------------------------------------- #

K8S_RULES: dict[str, Rule] = {
    "deployment": ("k8s.deployment", "Deployment", K.SERVICE),
    "statefulset": ("k8s.statefulset", "StatefulSet", K.SERVICE),
    "daemonset": ("k8s.daemonset", "DaemonSet", K.SERVICE),
    "replicaset": ("k8s.replicaset", "ReplicaSet", K.SERVICE),
    "pod": ("k8s.pod", "Pod", K.SERVICE),
    "job": ("k8s.job", "Job", K.WORKER),
    "cronjob": ("k8s.cronjob", "CronJob", K.WORKER),
    "service": ("k8s.service", "Service", K.GATEWAY),
    "ingress": ("k8s.ingress", "Ingress", K.GATEWAY),
    "persistentvolumeclaim": ("k8s.pvc", "PersistentVolumeClaim", K.STORAGE),
    "persistentvolume": ("k8s.pv", "PersistentVolume", K.STORAGE),
    "configmap": ("k8s.configmap", "ConfigMap", K.INFRA),
    "secret": ("k8s.secret", "Secret", K.AUTH),
    "horizontalpodautoscaler": ("k8s.hpa", "HorizontalPodAutoscaler", K.INFRA),
}


# --------------------------------------------------------------------------- #
# Docker image names -> services  (docker-compose service detection)
# --------------------------------------------------------------------------- #

DOCKER_IMAGE_RULES: dict[str, Rule] = {
    "postgres": ("onprem.postgresql", "PostgreSQL", K.DATABASE),
    "postgis": ("onprem.postgresql", "PostGIS", K.DATABASE),
    "mysql": ("onprem.mysql", "MySQL", K.DATABASE),
    "mariadb": ("onprem.mariadb", "MariaDB", K.DATABASE),
    "mongo": ("onprem.mongodb", "MongoDB", K.DATABASE),
    "redis": ("onprem.redis", "Redis", K.CACHE),
    "memcached": ("onprem.memcached", "Memcached", K.CACHE),
    "elasticsearch": ("onprem.elasticsearch", "Elasticsearch", K.SEARCH),
    "opensearchproject/opensearch": ("onprem.elasticsearch", "OpenSearch", K.SEARCH),
    "solr": ("onprem.solr", "Apache Solr", K.SEARCH),
    "rabbitmq": ("onprem.rabbitmq", "RabbitMQ", K.QUEUE),
    "nats": ("onprem.nats", "NATS", K.QUEUE),
    "kafka": ("onprem.kafka", "Apache Kafka", K.STREAM),
    "confluentinc/cp-kafka": ("onprem.kafka", "Apache Kafka", K.STREAM),
    "bitnami/kafka": ("onprem.kafka", "Apache Kafka", K.STREAM),
    "zookeeper": ("onprem.zookeeper", "ZooKeeper", K.INFRA),
    "nginx": ("onprem.nginx", "Nginx", K.GATEWAY),
    "traefik": ("onprem.traefik", "Traefik", K.GATEWAY),
    "haproxy": ("onprem.haproxy", "HAProxy", K.GATEWAY),
    "envoyproxy/envoy": ("onprem.envoy", "Envoy", K.GATEWAY),
    "kong": ("onprem.kong", "Kong", K.GATEWAY),
    "caddy": ("onprem.nginx", "Caddy", K.GATEWAY),
    "prom/prometheus": ("onprem.prometheus", "Prometheus", K.MONITORING),
    "grafana/grafana": ("onprem.grafana", "Grafana", K.MONITORING),
    "grafana/loki": ("onprem.loki", "Loki", K.MONITORING),
    "jaegertracing": ("generic.otel", "Jaeger", K.MONITORING),
    "minio/minio": ("onprem.minio", "MinIO", K.STORAGE),
    "hashicorp/vault": ("onprem.vault", "HashiCorp Vault", K.AUTH),
    "vault": ("onprem.vault", "HashiCorp Vault", K.AUTH),
    "quay.io/keycloak/keycloak": ("onprem.keycloak", "Keycloak", K.AUTH),
    "keycloak": ("onprem.keycloak", "Keycloak", K.AUTH),
    "clickhouse": ("onprem.clickhouse", "ClickHouse", K.DATABASE),
    "cassandra": ("onprem.cassandra", "Cassandra", K.DATABASE),
    "neo4j": ("onprem.neo4j", "Neo4j", K.DATABASE),
    "influxdb": ("onprem.influxdb", "InfluxDB", K.DATABASE),
    "localstack": ("aws.sdk", "LocalStack (AWS emulation)", K.INFRA),
    "apache/airflow": ("onprem.airflow", "Apache Airflow", K.WORKER),
    "mcr.microsoft.com/mssql": ("onprem.mssql", "SQL Server", K.DATABASE),
}


# --------------------------------------------------------------------------- #
# Content probes - regexes over raw file text, each with a confidence.
#
# The weight encodes a real distinction: a *call* is usage, a *string* is a
# mention.
#
#   CALL_PROBE (0.8) matches a code shape that only appears when the SDK is
#   actually invoked - `boto3.client("s3")`, `def lambda_handler(`. Hard to
#   produce by accident, so it may originate a component on its own.
#
#   MENTION_PROBE (0.6) matches a bare URL scheme or hostname - `s3://`,
#   `postgres://`. Those turn up in sample config, commented-out code, test
#   fixtures and lists of patterns. (This project's own catalog contains every
#   one of them, which is how the false positive was found.) Below the
#   component threshold, so it corroborates a stronger signal but never
#   invents infrastructure by itself.
# --------------------------------------------------------------------------- #

CALL_PROBE = 0.8
MENTION_PROBE = 0.6

CONTENT_PROBES: list[tuple[re.Pattern[str], Rule, float]] = [
    # -- connection strings: a mention, not proof of use ------------------
    (re.compile(r"\bpostgres(?:ql)?://", re.I), ("onprem.postgresql", "PostgreSQL", K.DATABASE), MENTION_PROBE),
    (re.compile(r"\bmysql://", re.I), ("onprem.mysql", "MySQL", K.DATABASE), MENTION_PROBE),
    (re.compile(r"\bmongodb(?:\+srv)?://", re.I), ("onprem.mongodb", "MongoDB", K.DATABASE), MENTION_PROBE),
    (re.compile(r"\bredis://", re.I), ("onprem.redis", "Redis", K.CACHE), MENTION_PROBE),
    (re.compile(r"\bamqps?://", re.I), ("onprem.rabbitmq", "RabbitMQ", K.QUEUE), MENTION_PROBE),
    (re.compile(r"\.blob\.core\.windows\.net", re.I), ("azure.blobstorage", "Blob Storage", K.STORAGE), MENTION_PROBE),
    (re.compile(r"\.queue\.core\.windows\.net", re.I), ("azure.queuestorage", "Queue Storage", K.QUEUE), MENTION_PROBE),
    (re.compile(r"\.servicebus\.windows\.net", re.I), ("azure.servicebus", "Service Bus", K.QUEUE), MENTION_PROBE),
    (re.compile(r"\.documents\.azure\.com", re.I), ("azure.cosmosdb", "Cosmos DB", K.DATABASE), MENTION_PROBE),
    (re.compile(r"\.database\.windows\.net", re.I), ("azure.sqldatabase", "Azure SQL Database", K.DATABASE), MENTION_PROBE),
    (re.compile(r"\.vault\.azure\.net", re.I), ("azure.keyvault", "Key Vault", K.AUTH), MENTION_PROBE),
    (re.compile(r"s3://|\.s3[.-][a-z0-9-]*\.amazonaws\.com", re.I), ("aws.s3", "Amazon S3", K.STORAGE), MENTION_PROBE),
    (re.compile(r"\.execute-api\.[a-z0-9-]+\.amazonaws\.com", re.I), ("aws.apigateway", "API Gateway", K.GATEWAY), MENTION_PROBE),
    (re.compile(r"\bstorage\.googleapis\.com|\bgs://", re.I), ("gcp.gcs", "Cloud Storage", K.STORAGE), MENTION_PROBE),
    (re.compile(r"\brun\.app\b", re.I), ("gcp.run", "Cloud Run", K.SERVICE), MENTION_PROBE),
    (re.compile(r"api\.anthropic\.com", re.I), ("saas.anthropic", "Claude API", K.EXTERNAL), MENTION_PROBE),
    (re.compile(r"api\.openai\.com", re.I), ("saas.openai", "OpenAI API", K.EXTERNAL), MENTION_PROBE),
    (re.compile(r"\bgraphql\b.{0,40}\bschema\b", re.I), ("programming.graphql", "GraphQL", K.GATEWAY), MENTION_PROBE),

    # -- call shapes: the code demonstrably invokes the service -----------
    (re.compile(r"boto3\.(?:client|resource)\(\s*['\"]s3['\"]", re.I), ("aws.s3", "Amazon S3", K.STORAGE), CALL_PROBE),
    (re.compile(r"boto3\.(?:client|resource)\(\s*['\"]dynamodb['\"]", re.I), ("aws.dynamodb", "DynamoDB", K.DATABASE), CALL_PROBE),
    (re.compile(r"boto3\.client\(\s*['\"]sqs['\"]", re.I), ("aws.sqs", "Amazon SQS", K.QUEUE), CALL_PROBE),
    (re.compile(r"boto3\.client\(\s*['\"]sns['\"]", re.I), ("aws.sns", "Amazon SNS", K.QUEUE), CALL_PROBE),
    (re.compile(r"boto3\.client\(\s*['\"]lambda['\"]", re.I), ("aws.lambda", "AWS Lambda", K.FUNCTION), CALL_PROBE),
    (re.compile(r"boto3\.client\(\s*['\"]kinesis['\"]", re.I), ("aws.kinesis", "Kinesis", K.STREAM), CALL_PROBE),
    (re.compile(r"boto3\.client\(\s*['\"]secretsmanager['\"]", re.I), ("aws.secretsmanager", "Secrets Manager", K.AUTH), CALL_PROBE),
    (re.compile(r"boto3\.client\(\s*['\"]bedrock", re.I), ("aws.bedrock", "Amazon Bedrock", K.ML), CALL_PROBE),
    (re.compile(r"def\s+lambda_handler\s*\(", re.I), ("aws.lambda", "AWS Lambda", K.FUNCTION), CALL_PROBE),
    # `func.HttpRequest` only appears in a real Azure Functions handler. The
    # bare token `azure.functions` is not used here: it is also this project's
    # own canonical service key, so it matches every registry table that names
    # it. Genuine imports are already covered by IMPORT_RULES.
    (re.compile(r"\bfunc\.HttpRequest\b|\bfunc\.HttpResponse\b"), ("azure.functions", "Azure Functions", K.FUNCTION), CALL_PROBE),
    (re.compile(r"anthropic\.(?:Anthropic|AsyncAnthropic)\(", re.I), ("saas.anthropic", "Claude API", K.EXTERNAL), CALL_PROBE),
    (re.compile(r"\bOpenAI\(\s*\)|openai\.(?:ChatCompletion|OpenAI)\(", re.I), ("saas.openai", "OpenAI API", K.EXTERNAL), CALL_PROBE),
    (re.compile(r"\bstripe\.(?:Charge|PaymentIntent|Customer|Subscription)\.", re.I), ("saas.stripe", "Stripe", K.EXTERNAL), CALL_PROBE),
]


# --------------------------------------------------------------------------- #
# Filename / path probes
# --------------------------------------------------------------------------- #

FILENAME_RULES: list[tuple[re.Pattern[str], Rule]] = [
    (re.compile(r"(^|/)dockerfile(\.|$)", re.I), ("onprem.docker", "Docker", K.INFRA)),
    (re.compile(r"(^|/)docker-compose[.\-\w]*\.ya?ml$", re.I), ("onprem.docker", "Docker Compose", K.INFRA)),
    (re.compile(r"(^|/)\.github/workflows/.+\.ya?ml$", re.I), ("onprem.githubactions", "GitHub Actions", K.CICD)),
    (re.compile(r"(^|/)\.gitlab-ci\.ya?ml$", re.I), ("onprem.gitlabci", "GitLab CI", K.CICD)),
    (re.compile(r"(^|/)jenkinsfile$", re.I), ("onprem.jenkins", "Jenkins", K.CICD)),
    (re.compile(r"(^|/)azure-pipelines[\w.-]*\.ya?ml$", re.I), ("azure.devops", "Azure Pipelines", K.CICD)),
    (re.compile(r"(^|/)\.circleci/config\.ya?ml$", re.I), ("onprem.circleci", "CircleCI", K.CICD)),
    (re.compile(r"\.tf$", re.I), ("onprem.terraform", "Terraform", K.CICD)),
    (re.compile(r"\.bicep$", re.I), ("azure.arm", "Bicep / ARM", K.CICD)),
    (re.compile(r"(^|/)serverless\.ya?ml$", re.I), ("aws.lambda", "Serverless Framework", K.FUNCTION)),
    (re.compile(r"(^|/)chart\.ya?ml$", re.I), ("onprem.helm", "Helm chart", K.CICD)),
    (re.compile(r"(^|/)kustomization\.ya?ml$", re.I), ("k8s.deployment", "Kustomize", K.CICD)),
    (re.compile(r"(^|/)ansible/|(^|/)playbook\.ya?ml$", re.I), ("onprem.ansible", "Ansible", K.CICD)),
    (re.compile(r"(^|/)next\.config\.[jtm]s$", re.I), ("programming.react", "Next.js", K.FRONTEND)),
    (re.compile(r"(^|/)nuxt\.config\.[jtm]s$", re.I), ("programming.vue", "Nuxt", K.FRONTEND)),
    (re.compile(r"(^|/)vite\.config\.[jtm]s$", re.I), ("generic.bundler", "Vite", K.FRONTEND)),
    (re.compile(r"(^|/)manage\.py$", re.I), ("programming.django", "Django", K.SERVICE)),
    (re.compile(r"(^|/)host\.json$", re.I), ("azure.functions", "Azure Functions", K.FUNCTION)),
    (re.compile(r"(^|/)template\.ya?ml$", re.I), ("aws.cloudformation", "AWS SAM / CloudFormation", K.CICD)),
]


# --------------------------------------------------------------------------- #
# Lookup helpers
# --------------------------------------------------------------------------- #


def normalise_package(name: str) -> str:
    """Fold a package name to the form used as a `DEPENDENCY_RULES` key."""
    return name.strip().lower().replace("_", "-").replace(".", "-")


def match_dependency(name: str) -> Rule | None:
    """Resolve a package name, preferring the most specific matching key."""
    norm = normalise_package(name)
    if norm in DEPENDENCY_IGNORE:
        return None
    if norm in DEPENDENCY_RULES:
        return DEPENDENCY_RULES[norm]
    # Scoped/namespaced packages: try progressively shorter prefixes.
    best: Rule | None = None
    best_len = 0
    for key, rule in DEPENDENCY_RULES.items():
        if norm.startswith(key) and len(key) > best_len:
            best, best_len = rule, len(key)
    return best


def match_import(module: str) -> Rule | None:
    """Resolve an import specifier (Python dotted path or JS module name)."""
    mod = module.strip().lower().replace("/", ".").lstrip(".")
    if not mod:
        return None
    best: Rule | None = None
    best_len = 0
    for key, rule in IMPORT_RULES.items():
        key_norm = key.replace("/", ".")
        if (mod == key_norm or mod.startswith(key_norm + ".")) and len(key_norm) > best_len:
            best, best_len = rule, len(key_norm)
    return best


def _longest_prefix(table: dict[str, Rule], value: str) -> Rule | None:
    best: Rule | None = None
    best_len = 0
    for key, rule in table.items():
        if value.startswith(key) and len(key) > best_len:
            best, best_len = rule, len(key)
    return best


def match_terraform(resource_type: str) -> Rule | None:
    return _longest_prefix(TERRAFORM_RULES, resource_type.strip().lower())


def match_cloudformation(resource_type: str) -> Rule | None:
    return CLOUDFORMATION_RULES.get(resource_type.strip())


def match_arm(resource_type: str) -> Rule | None:
    return _longest_prefix(ARM_RULES, resource_type.strip().lower())


def match_k8s(kind: str) -> Rule | None:
    return K8S_RULES.get(kind.strip().lower())


def match_docker_image(image: str) -> Rule | None:
    img = image.strip().lower().split(":", 1)[0]
    best: Rule | None = None
    best_len = 0
    for key, rule in DOCKER_IMAGE_RULES.items():
        # Match either the full path or the bare image name after the registry.
        tail = img.rsplit("/", 1)[-1]
        if (img.startswith(key) or tail == key) and len(key) > best_len:
            best, best_len = rule, len(key)
    return best
