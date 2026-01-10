"""
CRM app admin configuration
"""
from django.contrib import admin
from .models import Lead, LeadComment, LeadTask, Contact, Customer


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ('name', 'workspace', 'status', 'priority', 'assigned_to', 'received_at')
    list_filter = ('status', 'priority', 'source', 'workspace')
    search_fields = ('name', 'email', 'phone', 'message')
    date_hierarchy = 'received_at'


@admin.register(LeadComment)
class LeadCommentAdmin(admin.ModelAdmin):
    list_display = ('lead', 'author', 'comment_type', 'created_at')
    list_filter = ('comment_type', 'created_at')
    search_fields = ('content',)


@admin.register(LeadTask)
class LeadTaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'lead', 'assigned_to', 'status', 'due_date')
    list_filter = ('status', 'priority')
    search_fields = ('title', 'description')


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('first_name', 'last_name', 'email', 'phone', 'contact_type', 'workspace')
    list_filter = ('contact_type', 'status', 'workspace')
    search_fields = ('first_name', 'last_name', 'email', 'phone', 'company')


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('contact', 'workspace', 'customer_type', 'status', 'lifetime_value')
    list_filter = ('customer_type', 'status')
    search_fields = ('contact__first_name', 'contact__last_name', 'customer_number')
