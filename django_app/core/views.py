"""
Core app views - Public pages and authentication
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone

from .models import User
from workspaces.models import Workspace, WorkspaceMember, WorkspaceInvitation, WorkspaceRole


def home(request):
    """Landing page - redirect to workspace selection if logged in"""
    if request.user.is_authenticated:
        return redirect('core:workspace_list')
    return render(request, 'core/home.html')


def login_view(request):
    """User login"""
    if request.user.is_authenticated:
        return redirect('core:workspace_list')
    
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        password = request.POST.get('password', '')
        remember = request.POST.get('remember', False)
        
        user = authenticate(request, username=email, password=password)
        
        if user is not None:
            login(request, user)
            user.last_login_at = timezone.now()
            user.save(update_fields=['last_login_at'])
            
            if not remember:
                request.session.set_expiry(0)  # Session expires when browser closes
            
            next_url = request.GET.get('next', 'core:workspace_list')
            return redirect(next_url)
        else:
            messages.error(request, 'Invalid email or password')
    
    return render(request, 'core/login.html')


def logout_view(request):
    """User logout"""
    logout(request)
    messages.success(request, 'You have been logged out')
    return redirect('core:login')


def register_view(request):
    """User registration"""
    if request.user.is_authenticated:
        return redirect('core:workspace_list')
    
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        password = request.POST.get('password', '')
        password_confirm = request.POST.get('password_confirm', '')
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        
        # Validation
        if User.objects.filter(email=email).exists():
            messages.error(request, 'An account with this email already exists')
        elif password != password_confirm:
            messages.error(request, 'Passwords do not match')
        elif len(password) < 8:
            messages.error(request, 'Password must be at least 8 characters')
        else:
            user = User.objects.create_user(
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name
            )
            login(request, user)
            messages.success(request, 'Account created successfully!')
            return redirect('core:workspace_list')
    
    return render(request, 'core/register.html')


def password_reset_request(request):
    """Request password reset"""
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        # TODO: Implement password reset email
        messages.info(request, 'If an account exists with this email, you will receive a password reset link.')
    
    return render(request, 'core/password_reset.html')


def password_reset_confirm(request, uidb64, token):
    """Confirm password reset"""
    # TODO: Implement password reset confirmation
    return render(request, 'core/password_reset_confirm.html')


@login_required
def workspace_list(request):
    """List workspaces the user is a member of"""
    memberships = WorkspaceMember.objects.filter(
        user=request.user,
        is_active=True
    ).select_related('workspace', 'role')
    
    return render(request, 'core/workspace_list.html', {
        'memberships': memberships
    })


@login_required
def workspace_create(request):
    """Create a new workspace"""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        slug = request.POST.get('slug', '').strip().lower()
        description = request.POST.get('description', '').strip()
        
        if not name:
            messages.error(request, 'Workspace name is required')
        elif slug and Workspace.objects.filter(slug=slug).exists():
            messages.error(request, 'This URL slug is already taken')
        else:
            # Create workspace with optional slug
            workspace = Workspace(
                name=name,
                description=description,
                owner=request.user
            )
            if slug:
                workspace.slug = slug
            workspace.save()
            
            # Get or create admin role
            admin_role, _ = WorkspaceRole.objects.get_or_create(
                name='admin',
                defaults={
                    'display_name': 'Administrator',
                    'is_system_role': True,
                    'hierarchy_level': 1,
                    'can_manage_workspace': True,
                    'can_manage_users': True,
                    'can_manage_roles': True,
                    'can_manage_connections': True,
                    'can_view_all_leads': True,
                    'can_create_leads': True,
                    'can_edit_leads': True,
                    'can_delete_leads': True,
                    'can_assign_leads': True,
                    'can_view_all_listings': True,
                    'can_create_listings': True,
                    'can_edit_listings': True,
                    'can_delete_listings': True,
                    'can_publish_listings': True,
                    'can_view_analytics': True,
                    'can_export_data': True,
                }
            )
            
            # Add owner as admin member
            WorkspaceMember.objects.create(
                workspace=workspace,
                user=request.user,
                role=admin_role
            )
            
            messages.success(request, f'Workspace "{name}" created successfully!')
            return redirect('workspaces:dashboard', workspace_slug=workspace.slug)
    
    return render(request, 'core/workspace_create.html')


def accept_invitation(request, token):
    """Accept a workspace invitation"""
    invitation = get_object_or_404(
        WorkspaceInvitation,
        token=token,
        status='pending'
    )
    
    if invitation.is_expired:
        invitation.status = 'expired'
        invitation.save()
        messages.error(request, 'This invitation has expired')
        return redirect('core:login')
    
    # If user is not logged in, redirect to login/register
    if not request.user.is_authenticated:
        # Store invitation token in session
        request.session['pending_invitation'] = token
        messages.info(request, 'Please log in or create an account to accept this invitation')
        return redirect('core:login')
    
    # Check if user's email matches invitation
    if request.user.email.lower() != invitation.email.lower():
        messages.error(request, 'This invitation was sent to a different email address')
        return redirect('core:workspace_list')
    
    # Accept invitation
    if request.method == 'POST':
        # Create membership
        WorkspaceMember.objects.create(
            workspace=invitation.workspace,
            user=request.user,
            role=invitation.role,
            invited_by=invitation.invited_by,
            invited_at=invitation.created_at
        )
        
        invitation.status = 'accepted'
        invitation.accepted_at = timezone.now()
        invitation.save()
        
        messages.success(request, f'Welcome to {invitation.workspace.name}!')
        return redirect('workspaces:dashboard', workspace_slug=invitation.workspace.slug)
    
    return render(request, 'core/accept_invitation.html', {
        'invitation': invitation
    })
