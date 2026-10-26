from django.db import models

from django.conf import settings



class StudyDocument(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="study_documents"
    )
    title = models.CharField(max_length=200)
    pdf_file = models.FileField(
        upload_to="study_pdfs/",
        blank=True,
        null=True
    )
    source_text = models.TextField()
    status = models.CharField(
        max_length=20,
        default="ready"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title