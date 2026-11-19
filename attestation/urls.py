from django.urls import path

from attestation import views

urlpatterns = [
    path('nonce/', views.NonceView.as_view(), name='attest-nonce'),
    path('ios/', views.IOSAttestView.as_view(), name='attest-ios'),
    path('android/', views.AndroidAttestView.as_view(), name='attest-android'),
]
