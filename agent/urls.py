from django.urls import path

from agent import components, proposals, views

app_name = 'agent'
urlpatterns = [
    path('runs/', views.runs, name='runs'),
    path('start/', views.start, name='start'),
    path('attach/', views.attach, name='attach'),
    path('components/', components.render, name='component_render'),
    path('components/batch/', components.batch, name='component_batch'),
    path('components/pick/', components.pick, name='component_pick'),
    path('components/act/', components.act, name='component_act'),
    path('runs/<int:run_id>/', views.run, name='run'),
    path('runs/<int:run_id>/confirm/', views.confirm, name='confirm'),
    path('runs/<int:run_id>/cancel/', views.cancel, name='cancel'),
    path('runs/<int:run_id>/reply/', views.reply, name='reply'),
    path('runs/<int:run_id>/stop/', views.stop, name='stop'),
    path('runs/<int:run_id>/undo/', views.undo, name='undo'),
    path('runs/<int:run_id>/feedback/', views.feedback, name='feedback'),
    path('proposals/<int:proposal_id>/apply/', proposals.apply, name='proposal_apply'),
]
