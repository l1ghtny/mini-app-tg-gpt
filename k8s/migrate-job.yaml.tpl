apiVersion: batch/v1
kind: Job
metadata:
  # Unique per TeamCity build so Jobs don't clash
  name: tg-mini-backend-__JOB_SUFFIX__
  namespace: __K8S_NAMESPACE__
  labels:
    app: tg-mini-backend
    env: __DEPLOY_ENV__
spec:
  # auto-clean after N seconds once finished
  ttlSecondsAfterFinished: 600
  backoffLimit: 0
  template:
    metadata:
      labels:
        app: tg-mini-backend
        env: __DEPLOY_ENV__
        version: __IMAGE_TAG__
    spec:
      restartPolicy: Never
      containers:
        - name: migrate
          image: __IMAGE_REGISTRY__/__IMAGE_NAME__:__IMAGE_TAG__
          imagePullPolicy: IfNotPresent
          command: ["alembic", "upgrade", "head"]
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: __SECRET_NAME__
                  key: DATABASE_URL