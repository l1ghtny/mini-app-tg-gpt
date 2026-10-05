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
              # The shared DB also contains beta Work private originals.
              env:
                - name: R2_PRIVATE_DOCUMENTS_BUCKET
                  valueFrom:
                    secretKeyRef:
                      name: tg-mini-beta-work-runs-r2
                      key: R2_PRIVATE_DOCUMENTS_BUCKET
                      optional: true
                - name: R2_PRIVATE_DOCUMENTS_ACCESS_KEY_ID
                  valueFrom:
                    secretKeyRef:
                      name: tg-mini-beta-work-runs-r2
                      key: R2_PRIVATE_DOCUMENTS_ACCESS_KEY_ID
                      optional: true
                - name: R2_PRIVATE_DOCUMENTS_SECRET_ACCESS_KEY
                  valueFrom:
                    secretKeyRef:
                      name: tg-mini-beta-work-runs-r2
                      key: R2_PRIVATE_DOCUMENTS_SECRET_ACCESS_KEY
                      optional: true
                - name: R2_PRIVATE_DOCUMENTS_SESSION_TOKEN
                  valueFrom:
                    secretKeyRef:
                      name: tg-mini-beta-work-runs-r2
                      key: R2_PRIVATE_DOCUMENTS_SESSION_TOKEN
                      optional: true
                - name: R2_PRIVATE_DOCUMENTS_CREDENTIAL_EXPIRES_AT
                  valueFrom:
                    secretKeyRef:
                      name: tg-mini-beta-work-runs-r2
                      key: R2_PRIVATE_DOCUMENTS_CREDENTIAL_EXPIRES_AT
                      optional: true
              resources:
                requests: {cpu: 50m, memory: 128Mi}
                limits: {cpu: 250m, memory: 512Mi}
