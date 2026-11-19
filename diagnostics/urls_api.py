from django.urls import path

from diagnostics import api

app_name = 'diagnostics_api'
urlpatterns = [
    path('symbols/', api.SymbolsUpload.as_view(), name='symbols'),
    path('symbolicate/<int:report_id>/', api.SymbolicateCallback.as_view(), name='symbolicate'),
]
