apiVersion: batch/v1
kind: CronJob
metadata:
  name: cleanup-documents
spec:
  schedule: "17 * * * *"
  suspend: false
  concurrencyPolicy: Forbid
  startingDeadlineSeconds: 600
  successfulJobsHistoryLimit: 1
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 1
      activeDeadlineSeconds: 1200
      ttlSecondsAfterFinished: 86400
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: cleanup-documents
              image: __BACKEND_IMAGE__
              command: ["python", "jobs/cleanup_documents.py"]
              envFrom:
                - secretRef:
                    name: backend-env
              resources:
                requests: {cpu: 50m, memory: 128Mi}
                limits: {cpu: 250m, memory: 512Mi}
