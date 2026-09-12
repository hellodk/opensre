docker run -d --rm --name opensre-keycloak-live \
  -p 127.0.0.1:18080:8080 -p 127.0.0.1:19000:9000 \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
  -e KC_HEALTH_ENABLED=true -e KC_METRICS_ENABLED=true \
  -e KC_EVENT_METRICS_USER_ENABLED=true \
  quay.io/keycloak/keycloak:26.7.3 start-dev
