# k8s/migrate-job.yaml.tpl

apiVersion: batch/v1
kind: Job
metadata:
  # Unique per TeamCity build so Jobs don't clash
  name: tg-mini-backend-%build.id%
  namespace: %env.K8S_NAMESPACE%
  labels:
    app: tg-mini-backend
    env: %env.DEPLOY_ENV%
spec:
  # auto-clean after N seconds once finished
  ttlSecondsAfterFinished: 600
  backoffLimit: 0
  template:
    metadata:
      labels:
        app: tg-mini-backend
        env: %env.DEPLOY_ENV%
        version: %env.IMAGE_TAG%
    spec:
      restartPolicy: Never
      containers:
        - name: migrate
          image: %env.IMAGE_REGISTRY%/%env.IMAGE_NAME%:%dep.BUILD_NUMBER%
          imagePullPolicy: IfNotPresent
          command: ["alembic", "upgrade", "head"]
          env:
            # you can add more env vars here as needed
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: %env.SECRET_NAME%
                  key: DATABASE_URL
