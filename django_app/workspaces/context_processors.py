"""
Workspace context processors
Adds workspace-related context to all templates
"""


def workspace_context(request):
    """
    Add workspace context to all templates.
    
    Provides:
    - workspace: Current workspace object (if in a workspace context)
    - workspace_member: Current user's membership in the workspace
    - workspace_role: Current user's role in the workspace
    - user_workspaces: All workspaces the user is a member of
    """
    context = {
        'workspace': None,
        'workspace_member': None,
        'workspace_role': None,
        'user_workspaces': [],
    }
    
    # Get workspace from request (set by WorkspaceMiddleware)
    if hasattr(request, 'workspace'):
        context['workspace'] = request.workspace
    
    if hasattr(request, 'workspace_member'):
        context['workspace_member'] = request.workspace_member
        if request.workspace_member:
            context['workspace_role'] = request.workspace_member.role
    
    # Get user's workspaces if authenticated
    if request.user.is_authenticated:
        from workspaces.models import WorkspaceMember
        context['user_workspaces'] = WorkspaceMember.objects.filter(
            user=request.user,
            is_active=True
        ).select_related('workspace', 'role')
    
    return context
