from django.shortcuts import render


def home(request):
    return render(request, template_name='home/home.html')


def privacy(request):
    return render(request, template_name='home/privacy.html')
