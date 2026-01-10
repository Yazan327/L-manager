"""
Listings views
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone

from workspaces.views import workspace_required, permission_required
from .models import Listing, ListingFolder, ListingImage, LoopConfig


@workspace_required
def listing_list(request, workspace_slug):
    """List listings with filtering and pagination"""
    listings = Listing.objects.filter(
        workspace=request.workspace
    ).select_related('folder', 'owner', 'agent')
    
    # Filters
    status = request.GET.get('status', '')
    property_type = request.GET.get('property_type', '')
    offering_type = request.GET.get('offering_type', '')
    folder_id = request.GET.get('folder', '')
    search = request.GET.get('search', '')
    
    if status:
        listings = listings.filter(status=status)
    if property_type:
        listings = listings.filter(property_type=property_type)
    if offering_type:
        listings = listings.filter(offering_type=offering_type)
    if folder_id:
        listings = listings.filter(folder_id=folder_id)
    
    if search:
        listings = listings.filter(
            Q(title__icontains=search) |
            Q(reference_number__icontains=search) |
            Q(location_name__icontains=search) |
            Q(community__icontains=search)
        )
    
    listings = listings.order_by('-created_at')
    
    # Pagination
    paginator = Paginator(listings, 25)
    page = request.GET.get('page', 1)
    listings = paginator.get_page(page)
    
    # For filter dropdowns
    folders = ListingFolder.objects.filter(workspace=request.workspace)
    
    return render(request, 'listings/list.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'listings': listings,
        'folders': folders,
        'status_choices': Listing.STATUS_CHOICES,
        'property_type_choices': Listing.PROPERTY_TYPE_CHOICES,
        'offering_type_choices': Listing.OFFERING_TYPE_CHOICES,
        'current_filters': {
            'status': status,
            'property_type': property_type,
            'offering_type': offering_type,
            'folder': folder_id,
            'search': search,
        }
    })


@workspace_required
@permission_required('can_create_listings')
def listing_create(request, workspace_slug):
    """Create a new listing"""
    if request.method == 'POST':
        listing = Listing(
            workspace=request.workspace,
            reference_number=request.POST.get('reference_number', '').strip(),
            title=request.POST.get('title', '').strip(),
            description=request.POST.get('description', '').strip(),
            property_type=request.POST.get('property_type', 'apartment'),
            offering_type=request.POST.get('offering_type', 'sale'),
            price=request.POST.get('price', 0),
            bedrooms=request.POST.get('bedrooms') or None,
            bathrooms=request.POST.get('bathrooms') or None,
            size_sqft=request.POST.get('size_sqft') or None,
            location_name=request.POST.get('location_name', '').strip(),
            city=request.POST.get('city', '').strip(),
            community=request.POST.get('community', '').strip(),
            owner=request.user,
        )
        
        folder_id = request.POST.get('folder')
        if folder_id:
            listing.folder_id = folder_id
        
        listing.save()
        
        messages.success(request, f'Listing "{listing.reference_number}" created')
        return redirect('workspaces:listings:detail', workspace_slug=workspace_slug, listing_id=listing.id)
    
    folders = ListingFolder.objects.filter(workspace=request.workspace)
    
    return render(request, 'listings/form.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'folders': folders,
        'property_type_choices': Listing.PROPERTY_TYPE_CHOICES,
        'offering_type_choices': Listing.OFFERING_TYPE_CHOICES,
        'is_create': True,
    })


@workspace_required
def listing_detail(request, workspace_slug, listing_id):
    """View listing details"""
    listing = get_object_or_404(
        Listing.objects.select_related('folder', 'owner', 'agent').prefetch_related('images', 'videos'),
        id=listing_id,
        workspace=request.workspace
    )
    
    # Get leads for this listing
    leads = listing.leads.all()[:10]
    
    return render(request, 'listings/detail.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'listing': listing,
        'leads': leads,
    })


@workspace_required
@permission_required('can_edit_listings')
def listing_edit(request, workspace_slug, listing_id):
    """Edit listing"""
    listing = get_object_or_404(Listing, id=listing_id, workspace=request.workspace)
    
    if request.method == 'POST':
        listing.title = request.POST.get('title', listing.title).strip()
        listing.description = request.POST.get('description', '').strip()
        listing.property_type = request.POST.get('property_type', listing.property_type)
        listing.offering_type = request.POST.get('offering_type', listing.offering_type)
        listing.price = request.POST.get('price', listing.price)
        listing.bedrooms = request.POST.get('bedrooms') or None
        listing.bathrooms = request.POST.get('bathrooms') or None
        listing.size_sqft = request.POST.get('size_sqft') or None
        listing.location_name = request.POST.get('location_name', '').strip()
        listing.city = request.POST.get('city', '').strip()
        listing.community = request.POST.get('community', '').strip()
        listing.status = request.POST.get('status', listing.status)
        
        folder_id = request.POST.get('folder')
        listing.folder_id = folder_id if folder_id else None
        
        listing.save()
        
        messages.success(request, 'Listing updated')
        return redirect('workspaces:listings:detail', workspace_slug=workspace_slug, listing_id=listing.id)
    
    folders = ListingFolder.objects.filter(workspace=request.workspace)
    
    return render(request, 'listings/form.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'listing': listing,
        'folders': folders,
        'property_type_choices': Listing.PROPERTY_TYPE_CHOICES,
        'offering_type_choices': Listing.OFFERING_TYPE_CHOICES,
        'status_choices': Listing.STATUS_CHOICES,
        'is_create': False,
    })


@workspace_required
@permission_required('can_delete_listings')
def listing_delete(request, workspace_slug, listing_id):
    """Delete listing"""
    listing = get_object_or_404(Listing, id=listing_id, workspace=request.workspace)
    
    if request.method == 'POST':
        ref = listing.reference_number
        listing.delete()
        messages.success(request, f'Listing "{ref}" deleted')
        return redirect('workspaces:listings:list', workspace_slug=workspace_slug)
    
    return render(request, 'listings/delete_confirm.html', {
        'workspace': request.workspace,
        'listing': listing,
    })


@workspace_required
@permission_required('can_publish_listings')
@require_POST
def listing_publish(request, workspace_slug, listing_id):
    """Publish listing to PropertyFinder"""
    listing = get_object_or_404(Listing, id=listing_id, workspace=request.workspace)
    
    # TODO: Call PropertyFinder API to publish
    listing.status = 'active'
    listing.published_at = timezone.now()
    listing.save()
    
    messages.success(request, 'Listing published')
    return redirect('workspaces:listings:detail', workspace_slug=workspace_slug, listing_id=listing.id)


@workspace_required
@permission_required('can_publish_listings')
@require_POST
def listing_unpublish(request, workspace_slug, listing_id):
    """Unpublish listing"""
    listing = get_object_or_404(Listing, id=listing_id, workspace=request.workspace)
    
    # TODO: Call PropertyFinder API to unpublish
    listing.status = 'inactive'
    listing.save()
    
    messages.success(request, 'Listing unpublished')
    return redirect('workspaces:listings:detail', workspace_slug=workspace_slug, listing_id=listing.id)


@workspace_required
@permission_required('can_edit_listings')
def listing_images(request, workspace_slug, listing_id):
    """Manage listing images"""
    listing = get_object_or_404(Listing, id=listing_id, workspace=request.workspace)
    
    if request.method == 'POST':
        # Handle image upload
        images = request.FILES.getlist('images')
        for img in images:
            ListingImage.objects.create(
                listing=listing,
                image=img,
                order=listing.images.count()
            )
        messages.success(request, f'{len(images)} images uploaded')
        return redirect('workspaces:listings:images', workspace_slug=workspace_slug, listing_id=listing.id)
    
    images = listing.images.all().order_by('order')
    
    return render(request, 'listings/images.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'listing': listing,
        'images': images,
    })


@workspace_required
def folder_list(request, workspace_slug):
    """List listing folders"""
    folders = ListingFolder.objects.filter(
        workspace=request.workspace
    ).annotate(
        listing_count=Count('listings')
    ).order_by('order', 'name')
    
    return render(request, 'listings/folders.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'folders': folders,
    })


@workspace_required
@permission_required('can_edit_listings')
def folder_create(request, workspace_slug):
    """Create listing folder"""
    if request.method == 'POST':
        folder = ListingFolder.objects.create(
            workspace=request.workspace,
            name=request.POST.get('name', '').strip(),
            description=request.POST.get('description', '').strip(),
            color=request.POST.get('color', '#3B82F6'),
        )
        messages.success(request, f'Folder "{folder.name}" created')
        return redirect('workspaces:listings:folders', workspace_slug=workspace_slug)
    
    return render(request, 'listings/folder_form.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'is_create': True,
    })


@workspace_required
@permission_required('can_edit_listings')
def folder_edit(request, workspace_slug, folder_id):
    """Edit listing folder"""
    folder = get_object_or_404(ListingFolder, id=folder_id, workspace=request.workspace)
    
    if request.method == 'POST':
        folder.name = request.POST.get('name', folder.name).strip()
        folder.description = request.POST.get('description', '').strip()
        folder.color = request.POST.get('color', folder.color)
        folder.save()
        messages.success(request, 'Folder updated')
        return redirect('workspaces:listings:folders', workspace_slug=workspace_slug)
    
    return render(request, 'listings/folder_form.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'folder': folder,
        'is_create': False,
    })


@workspace_required
@permission_required('can_delete_listings')
def folder_delete(request, workspace_slug, folder_id):
    """Delete listing folder"""
    folder = get_object_or_404(ListingFolder, id=folder_id, workspace=request.workspace)
    
    if request.method == 'POST':
        name = folder.name
        # Move listings to no folder before deleting
        folder.listings.update(folder=None)
        folder.delete()
        messages.success(request, f'Folder "{name}" deleted')
        return redirect('workspaces:listings:folders', workspace_slug=workspace_slug)
    
    return render(request, 'listings/folder_delete_confirm.html', {
        'workspace': request.workspace,
        'folder': folder,
    })


@workspace_required
@permission_required('can_create_listings')
def bulk_upload(request, workspace_slug):
    """Bulk upload listings from CSV/JSON"""
    if request.method == 'POST':
        file = request.FILES.get('file')
        if file:
            # TODO: Process bulk upload file
            messages.info(request, 'Bulk upload processing not yet implemented')
        return redirect('workspaces:listings:list', workspace_slug=workspace_slug)
    
    return render(request, 'listings/bulk_upload.html', {
        'workspace': request.workspace,
        'membership': request.membership,
    })


@workspace_required
def loop_list(request, workspace_slug):
    """List loop configurations"""
    loops = LoopConfig.objects.filter(workspace=request.workspace)
    
    return render(request, 'listings/loops.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'loops': loops,
    })


@workspace_required
@permission_required('can_edit_listings')
def loop_create(request, workspace_slug):
    """Create loop configuration"""
    if request.method == 'POST':
        loop = LoopConfig.objects.create(
            workspace=request.workspace,
            name=request.POST.get('name', '').strip(),
            description=request.POST.get('description', '').strip(),
            interval_hours=int(request.POST.get('interval_hours', 24)),
            action=request.POST.get('action', 'refresh'),
        )
        messages.success(request, f'Loop "{loop.name}" created')
        return redirect('workspaces:listings:loops', workspace_slug=workspace_slug)
    
    return render(request, 'listings/loop_form.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'action_choices': LoopConfig.ACTION_CHOICES,
        'is_create': True,
    })


@workspace_required
@permission_required('can_edit_listings')
def loop_edit(request, workspace_slug, loop_id):
    """Edit loop configuration"""
    loop = get_object_or_404(LoopConfig, id=loop_id, workspace=request.workspace)
    
    if request.method == 'POST':
        loop.name = request.POST.get('name', loop.name).strip()
        loop.description = request.POST.get('description', '').strip()
        loop.interval_hours = int(request.POST.get('interval_hours', loop.interval_hours))
        loop.action = request.POST.get('action', loop.action)
        loop.is_active = request.POST.get('is_active') == 'on'
        loop.save()
        messages.success(request, 'Loop updated')
        return redirect('workspaces:listings:loops', workspace_slug=workspace_slug)
    
    return render(request, 'listings/loop_form.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'loop': loop,
        'action_choices': LoopConfig.ACTION_CHOICES,
        'is_create': False,
    })


@workspace_required
@permission_required('can_manage_connections')
@require_POST
def api_sync_listings(request, workspace_slug):
    """Sync listings with PropertyFinder API"""
    # TODO: Implement PropertyFinder API sync
    
    return JsonResponse({
        'success': True,
        'message': 'Listing sync initiated',
        'synced_count': 0
    })


# Import Count for folder_list
from django.db.models import Count
