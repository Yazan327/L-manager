"""
Workspace middleware for multi-tenant support
"""
from django.shortcuts import redirect
from django.urls import reverse
from django.contrib import messages


class WorkspaceMiddleware:
    """
    Middleware to handle workspace context for workspace-scoped routes
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Pre-process request
        request.workspace = None
        request.membership = None
        
        response = self.get_response(request)
        return response
