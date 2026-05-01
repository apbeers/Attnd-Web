from django.urls import path

from attestation import views

urlpatterns = [
    path('nonce/', views.NonceView.as_view(), name='attest-nonce'),
    path('ios/', views.IOSAttestView.as_view(), name='attest-ios'),
    path('ios/assert/', views.IOSAssertView.as_view(), name='attest-ios-assert'),
    path('android/', views.AndroidAttestView.as_view(), name='attest-android'),
]
