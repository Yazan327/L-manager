"""
CRM - Contacts views
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q

from workspaces.views import workspace_required, permission_required
from .models import Contact


@workspace_required
def contact_list(request, workspace_slug):
    """List contacts with filtering and pagination"""
    contacts = Contact.objects.filter(workspace=request.workspace)
    
    # Filters
    contact_type = request.GET.get('type', '')
    status = request.GET.get('status', '')
    search = request.GET.get('search', '')
    
    if contact_type:
        contacts = contacts.filter(contact_type=contact_type)
    if status:
        contacts = contacts.filter(status=status)
    
    if search:
        contacts = contacts.filter(
            Q(first_name__icontains=search) |
            Q(last_name__icontains=search) |
            Q(email__icontains=search) |
            Q(phone__icontains=search) |
            Q(company__icontains=search)
        )
    
    contacts = contacts.order_by('first_name', 'last_name')
    
    # Pagination
    paginator = Paginator(contacts, 25)
    page = request.GET.get('page', 1)
    contacts = paginator.get_page(page)
    
    return render(request, 'crm/contacts/list.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'contacts': contacts,
        'type_choices': Contact.CONTACT_TYPE_CHOICES,
        'status_choices': Contact.STATUS_CHOICES,
        'current_filters': {
            'type': contact_type,
            'status': status,
            'search': search,
        }
    })


@workspace_required
def contact_create(request, workspace_slug):
    """Create a new contact"""
    if request.method == 'POST':
        contact = Contact.objects.create(
            workspace=request.workspace,
            first_name=request.POST.get('first_name', '').strip(),
            last_name=request.POST.get('last_name', '').strip(),
            email=request.POST.get('email', '').strip(),
            phone=request.POST.get('phone', '').strip(),
            company=request.POST.get('company', '').strip(),
            job_title=request.POST.get('job_title', '').strip(),
            contact_type=request.POST.get('contact_type', 'buyer'),
            notes=request.POST.get('notes', '').strip(),
            owner=request.user,
        )
        
        messages.success(request, f'Contact "{contact.full_name}" created')
        return redirect('workspaces:contacts:detail', workspace_slug=workspace_slug, contact_id=contact.id)
    
    return render(request, 'crm/contacts/form.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'type_choices': Contact.CONTACT_TYPE_CHOICES,
        'is_create': True,
    })


@workspace_required
def contact_detail(request, workspace_slug, contact_id):
    """View contact details"""
    contact = get_object_or_404(
        Contact.objects.select_related('owner'),
        id=contact_id,
        workspace=request.workspace
    )
    
    # Get related leads
    leads = contact.get_leads()
    
    return render(request, 'crm/contacts/detail.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'contact': contact,
        'leads': leads,
    })


@workspace_required
def contact_edit(request, workspace_slug, contact_id):
    """Edit contact"""
    contact = get_object_or_404(Contact, id=contact_id, workspace=request.workspace)
    
    if request.method == 'POST':
        contact.first_name = request.POST.get('first_name', contact.first_name).strip()
        contact.last_name = request.POST.get('last_name', '').strip()
        contact.email = request.POST.get('email', '').strip()
        contact.phone = request.POST.get('phone', '').strip()
        contact.company = request.POST.get('company', '').strip()
        contact.job_title = request.POST.get('job_title', '').strip()
        contact.contact_type = request.POST.get('contact_type', contact.contact_type)
        contact.status = request.POST.get('status', contact.status)
        contact.notes = request.POST.get('notes', '').strip()
        contact.save()
        
        messages.success(request, 'Contact updated')
        return redirect('workspaces:contacts:detail', workspace_slug=workspace_slug, contact_id=contact.id)
    
    return render(request, 'crm/contacts/form.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'contact': contact,
        'type_choices': Contact.CONTACT_TYPE_CHOICES,
        'status_choices': Contact.STATUS_CHOICES,
        'is_create': False,
    })


@workspace_required
def contact_delete(request, workspace_slug, contact_id):
    """Delete contact"""
    contact = get_object_or_404(Contact, id=contact_id, workspace=request.workspace)
    
    if request.method == 'POST':
        name = contact.full_name
        contact.delete()
        messages.success(request, f'Contact "{name}" deleted')
        return redirect('workspaces:contacts:list', workspace_slug=workspace_slug)
    
    return render(request, 'crm/contacts/delete_confirm.html', {
        'workspace': request.workspace,
        'contact': contact,
    })
