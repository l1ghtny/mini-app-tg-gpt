apiVersion: batch/v1
kind: Job
metadata:
  name: __JOB_NAME__
spec:
  backoffLimit: 1
  activeDeadlineSeconds: 600
  ttlSecondsAfterFinished: 86400
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: retention-grace
          image: __BACKEND_IMAGE__
          command: ["python", "jobs/migrate_document_retention.py"]
          envFrom:
            - secretRef:
                name: backend-env
          resources:
            requests: {cpu: 50m, memory: 128Mi}
            limits: {cpu: 250m, memory: 512Mi}
