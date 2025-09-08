import csv
import random
from django.db.models import Q
from django.contrib.auth import authenticate
import numpy as np
import pandas as pd
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .serializers import UserProfileSerializer, ProductSerializer, OrderSerializer, ReturnSerializer
from .models import SalesForecastModel, UserProfile, Product, Order, Return
from .models import Business
from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import get_object_or_404
from django.http import HttpResponse, JsonResponse
from collections import defaultdict
from django.db.models import Count, F
from datetime import datetime, date, timedelta
from django.db.models import Sum, DecimalField 
from collections import OrderedDict
from decimal import Decimal
from sklearn.linear_model import LinearRegression 
from sklearn.preprocessing import PolynomialFeatures
from sklearn.pipeline import Pipeline
from django.db.models.functions import TruncDate
from django.core.mail import send_mail
from django.urls import reverse
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sites.shortcuts import get_current_site
from django.template.loader import render_to_string

# -------------------------
# USER AUTH
# -------------------------
@api_view(["POST"])
@permission_classes([AllowAny])
def signup(request):
    serializer = UserProfileSerializer(data=request.data)
    if serializer.is_valid():
        try:
            user = serializer.save()
            refresh = RefreshToken.for_user(user)
            return Response({
                "status": "success",
                "message": "User created successfully!",
                "user_id": user.id,
                "username": user.username,
                "access_token": str(refresh.access_token),
                "refresh_token": str(refresh),
            }, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({
                "status": "error",
                "message": f"Signup failed: {str(e)}"
            }, status=status.HTTP_400_BAD_REQUEST)
    else:
        mapped = {}
        for field, msgs in serializer.errors.items():
            if isinstance(msgs, list) and msgs:
                mapped[field] = msgs[0]
            elif isinstance(msgs, dict):
                mapped[field] = next(iter(msgs.values()))
            else:
                mapped[field] = str(msgs)
        return Response({"status": "error", "errors": mapped}, status=status.HTTP_400_BAD_REQUEST)

@api_view(["POST"])
@permission_classes([AllowAny])
def login(request):
    username_or_email = request.data.get("username", "")
    password = request.data.get("password", "")

    if not username_or_email or not password:
        return Response({"status": "error", "message": "Username/Email and password are required"},
                         status=status.HTTP_400_BAD_REQUEST)

    try:
        user_obj = UserProfile.objects.get(Q(username__iexact=username_or_email) | Q(email__iexact=username_or_email))
        uname = user_obj.username
    except UserProfile.DoesNotExist:
        return Response({"status": "error", "message": "Invalid username or password"},
                         status=status.HTTP_401_UNAUTHORIZED)

    user = authenticate(username=uname, password=password)
    if not user:
        return Response({"status": "error", "message": "Invalid username or password"},
                         status=status.HTTP_401_UNAUTHORIZED)

    refresh = RefreshToken.for_user(user)
    return Response({
        "status": "success",
        "message": "Login successful",
        "user_id": user.id,
        "username": user.username,
        "access_token": str(refresh.access_token),
        "refresh_token": str(refresh),
    }, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([AllowAny])
def logout(request):
    refresh_token = request.data.get("refresh_token")
    if not refresh_token:
        return Response({"status": "success", "message": "Logged out on client"}, status=status.HTTP_200_OK)
    try:
        token = RefreshToken(refresh_token)
        token.blacklist()
        return Response({"status": "success", "message": "Logged out successfully"}, status=status.HTTP_200_OK)
    except TokenError:
        return Response({"status": "error", "message": "Invalid or expired refresh token"}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_user_businesses(request):
    user = request.user
    businesses = user.businesses.all() if hasattr(user, "businesses") else []
    data = [
        {"id": b.id, "business_name": b.business_name or f"Business {b.id}"}
        for b in businesses
    ]
    return Response(data)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def add_business(request):
    user = request.user
    payload = request.data or {}
    try:
      biz = Business.objects.create(
        owner=user,
        business_name=payload.get("business_name", "").strip() or None,
        business_type=payload.get("business_type", "").strip() or None,
        contact_number=payload.get("contact_number", "").strip() or None,
        gst_tax_id=payload.get("gst_tax_id", "").strip() or None,
        business_address=payload.get("business_address", "").strip() or None,
        department_branch=payload.get("department_branch", "").strip() or None,
      )
      # Optional: copy products from an existing business owned by the user
      source_id = payload.get("copy_from_business")
      copied_count = 0
      if source_id:
        try:
          source = Business.objects.get(owner=user, id=int(source_id))
          from .models import Product
          source_products = Product.objects.filter(business=source)
          to_create = []
          for sp in source_products:
            to_create.append(Product(
              business=biz,
              product_name=sp.product_name,
              sku=sp.sku,
              category=sp.category,
              current_stock=sp.current_stock,
              min_stock=sp.min_stock,
              max_stock=sp.max_stock,
              price=sp.price,
              selling_price=sp.selling_price,
              supplier=sp.supplier,
            ))
          if to_create:
            Product.objects.bulk_create(to_create, ignore_conflicts=True)
            copied_count = len(to_create)
        except Exception:
          # Ignore copy failures and still return created business
          pass
      return Response({"id": biz.id, "business_name": biz.business_name, "copied_products": copied_count}, status=201)
    except Exception as e:
      return Response({"error": str(e)}, status=400)
    

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def dashboard_metrics(request):
    """
    Returns JSON with keys:
    total_sales, total_orders, net_profit, total_returns,
    top_sales, low_stock_products, sales_chart_data, category_chart_data
    """
    user = request.user
    user_businesses = user.businesses.all() if hasattr(user, "businesses") else []
    if not user_businesses:
        return Response({"error": "No business found for this user."}, status=400)

    # business query param: id or 'all'
    business_param = request.GET.get("business")
    if business_param and business_param != "all":
        try:
            business_ids = [int(business_param)] if user_businesses.filter(id=int(business_param)).exists() else []
        except Exception:
            return Response({"error": "Invalid business id"}, status=400)
        if not business_ids:
            return Response({"error": "Business not found for user"}, status=404)
    else:
        business_ids = list(user_businesses.values_list("id", flat=True))

    # Load querysets
    today = date.today()
    orders_qs = Order.objects.filter(business_id__in=business_ids, date=today)
    returns_qs = Return.objects.filter(business_id__in=business_ids, date=today)


    # Price lookups (maps by product_name)
    products = Product.objects.filter(business_id__in=business_ids)
    price_map = {p.product_name: Decimal(str(p.price)) for p in products}
    sp_map = {p.product_name: Decimal(str(p.selling_price)) for p in products}

    # Totals
    total_sales = Decimal("0.00")
    net_profit = Decimal("0.00")

    for o in orders_qs:
        sp = sp_map.get(o.product_name, Decimal("0.00"))
        cp = price_map.get(o.product_name, Decimal("0.00"))
        total_sales += sp * o.quantity
        net_profit += (sp - cp) * o.quantity

    for r in returns_qs:
        sp = sp_map.get(r.product_name, Decimal("0.00"))
        cp = price_map.get(r.product_name, Decimal("0.00"))
        total_sales -= sp * r.quantity
        net_profit -= (sp - cp) * r.quantity


    # Top sales (quantity net = orders - returns)
    qty_by_product = defaultdict(int)
    for o in orders_qs:
        qty_by_product[o.product_name] += o.quantity
    for r in returns_qs:
        qty_by_product[r.product_name] -= r.quantity

    top_sales = []
    for name, qty in qty_by_product.items():
        if qty > 0:
            revenue = float((sp_map.get(name, Decimal("0.00")) * qty))
            top_sales.append({"product_name": name, "quantity": qty, "revenue": round(revenue, 2)})
    top_sales = sorted(top_sales, key=lambda x: (-x["quantity"], x["product_name"]))[:5]

    # Low stock products
    low_stock_qs = products.filter(current_stock__lte=F("min_stock")).values("product_name", "current_stock", "min_stock")[:50]
    low_stock_products = list(low_stock_qs)

    # Sales chart data (last 30 days)
    days = int(request.GET.get("days", 30))
    if days <= 0 or days > 365:
        days = 30
    start = date.today() - timedelta(days=days - 1)
    # prefill dates with 0
    daily = { (start + timedelta(days=i)).isoformat(): 0.0 for i in range(days) }

    # Query full range for chart data (not only today)
    orders_range_qs = Order.objects.filter(business_id__in=business_ids, date__gte=start, date__lte=today)
    returns_range_qs = Return.objects.filter(business_id__in=business_ids, date__gte=start, date__lte=today)

    for o in orders_range_qs:
        sp = float(sp_map.get(o.product_name, Decimal("0.00")))
        daily[o.date.isoformat()] = daily.get(o.date.isoformat(), 0.0) + sp * o.quantity
    for r in returns_range_qs:
        sp = float(sp_map.get(r.product_name, Decimal("0.00")))
        daily[r.date.isoformat()] = daily.get(r.date.isoformat(), 0.0) - sp * r.quantity

    sales_chart_data = [{"date": d, "sales": round(v, 2)} for d, v in sorted(daily.items())]

    # Category distribution for products
    cat_counts = products.values("category").annotate(count=Count("id"))
    category_chart_data = [{"category": c["category"], "count": c["count"]} for c in cat_counts]

    return Response({
        "total_sales": round(float(total_sales), 2),
        "total_orders": orders_qs.count(),
        "net_profit": round(float(net_profit), 2),
        "total_returns": returns_qs.count(),
        "top_sales": top_sales,
        "low_stock_products": low_stock_products,
        "sales_chart_data": sales_chart_data,
        "category_chart_data": category_chart_data,
    })




# -------------------------
# PRODUCTS
# -------------------------
@api_view(['GET', 'POST', 'PUT'])
@permission_classes([IsAuthenticated])
def product_list(request, pk=None):
    user_businesses = request.user.businesses.all()
    if not user_businesses:
        return Response({"message": "No business found for this user."}, status=status.HTTP_403_FORBIDDEN)
    business_param = request.GET.get("business")
    if business_param and business_param != "all":
        try:
            business = user_businesses.get(id=int(business_param))
            business_ids = [business.id]
        except Exception:
            return Response({"message": "Invalid business id"}, status=status.HTTP_400_BAD_REQUEST)
    else:
        business_ids = list(user_businesses.values_list("id", flat=True))
    
    if request.method == 'GET':
        products = Product.objects.filter(business_id__in=business_ids)
        serializer = ProductSerializer(products, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    elif request.method == 'POST':
        # Handles adding a new product
        # Determine target business from query param or fall back to first
        business_param = request.GET.get("business") or request.data.get("business")
        target_business = None
        if business_param and business_param != 'all':
            try:
                target_business = user_businesses.get(id=int(business_param))
            except Exception:
                return Response({"message": "Invalid business id"}, status=status.HTTP_400_BAD_REQUEST)
        if target_business is None:
            target_business = user_businesses.first()

        serializer = ProductSerializer(data=request.data, context={'request': request, 'business_for_validation': target_business})
        if serializer.is_valid():
            serializer.save(business=target_business)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    elif request.method == 'PUT':
        # 🟢 CORRECTED: Handles editing an existing product
        product = get_object_or_404(Product, pk=pk, business__in=user_businesses)
        serializer = ProductSerializer(product, data=request.data, partial=True, context={'request': request, 'business_for_validation': product.business})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    

@api_view(['POST', 'PUT'])
@permission_classes([IsAuthenticated])
def add_edit_product(request):
    business = request.user.businesses.first()
    if not business:
        return Response({'error': 'No business found for this user'}, status=400)

    if request.method == 'POST':
        data = request.data.copy()
        data['business'] = business.id

        if Product.objects.filter(business=business, sku=data.get('sku')).exists():
            return Response({'sku': 'SKU already exists for your business!'}, status=400)

        serializer = ProductSerializer(data=data, context={"request": request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=400)

    elif request.method == 'PUT':
        product_id = request.data.get("id")
        if not product_id:
            return Response({"error": "Product ID is required for editing"}, status=400)

        product = get_object_or_404(Product, id=product_id, business=business)

        new_sku = request.data.get('sku', product.sku)
        if Product.objects.filter(business=business, sku=new_sku).exclude(id=product.id).exists():
            return Response({'sku': 'SKU already exists for your business!'}, status=400)

        serializer = ProductSerializer(product, data=request.data, partial=True, context={"request": request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)

@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_product(request, sku):
    business = request.user.businesses.first()
    if not business:
        return Response({'error': 'No business found for this user'}, status=400)

    try:
        product = Product.objects.get(sku=sku, business=business)
        product.delete()
        return Response({'message': 'Product deleted'})
    except Product.DoesNotExist:
        return Response({'error': 'Product not found'}, status=404)

# -----------------------
# ORDERS
# -----------------------
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def orders_list(request):
    user_businesses = request.user.businesses.all()
    if not user_businesses:
        return Response([], status=status.HTTP_200_OK)
    
    date_str = request.query_params.get('date')
    business_param = request.query_params.get('business')
    
    if business_param and business_param != 'all':
        try:
            b_id = int(business_param)
            if not user_businesses.filter(id=b_id).exists():
                return Response({"error": "Invalid business id"}, status=400)
            orders = Order.objects.filter(business_id=b_id)
        except Exception:
            return Response({"error": "Invalid business id"}, status=400)
    else:
        orders = Order.objects.filter(business__in=user_businesses)
    
    if date_str:
        try:
            filter_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            orders = orders.filter(date=filter_date)
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)

    serializer = OrderSerializer(orders, many=True)
    return Response(serializer.data)

@api_view(['POST', 'PUT'])
@permission_classes([IsAuthenticated])
def add_edit_order(request):
    try:
        business = request.user.businesses.first()
        if not business:
            return Response({'status': 'error', 'message': 'No business found for this user'}, status=400)
        
        if request.method == 'POST':
            # Determine business from param/body to match the product scope
            business_param = request.GET.get('business') or request.data.get('business')
            scoped_business = business
            if business_param and business_param != 'all':
                scoped_business = get_object_or_404(Business, id=int(business_param), owner=request.user)

            # Get the product name and quantity from the request
            product_name = request.data.get('product_name')
            quantity = request.data.get('quantity')

            tracking_id = request.data.get('tracking_id')
            # Ensure the product exists before trying to add an order (scoped)
            product = get_object_or_404(Product, product_name=product_name, business=scoped_business)

            # Check if there is enough stock
            if product.current_stock < quantity:
                return Response({'status': 'error', 'message': f'Not enough stock for {product_name}.'}, status=400)

            serializer = OrderSerializer(data=request.data, context={'request': request, 'business_for_create': scoped_business})
            if serializer.is_valid():
                order = serializer.save()

                # Update the product's stock after a successful order
                product.current_stock -= quantity
                product.save()

                return Response({'status': 'success', 'data': serializer.data}, status=201)
            return Response({'status': 'error', 'errors': serializer.errors}, status=400)
        
        elif request.method == 'PUT':
            order_id = request.data.get('id')
            if not order_id:
                return Response({'status': 'error', 'message': 'Order ID is required for editing'}, status=400)

            order = get_object_or_404(Order, id=order_id, business=business)
            serializer = OrderSerializer(order, data=request.data, partial=True, context={'request': request})
            if serializer.is_valid():
                serializer.save()
                return Response({'status': 'success', 'data': serializer.data})
            return Response({'status': 'error', 'errors': serializer.errors}, status=400)

    except ObjectDoesNotExist:
        return Response({'status': 'error', 'message': f'Product with name "{product_name}" not found.'}, status=404)
    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=400)

@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_order(request, pk):
    """
    Deletes a specific order and restores the product stock.
    The order must belong to the authenticated user's business.
    """
    try:
        # Fetch by PK first to handle historical records possibly missing business
        order = get_object_or_404(Order, pk=pk)
        # Enforce permission: order must belong to one of user's businesses (if set)
        user_businesses = request.user.businesses.all()
        if order.business and order.business not in user_businesses:
            return Response(
                {"error": "You do not have permission to delete this order, or it does not exist."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # 🟢 CRITICAL CHANGE: Get product and quantity BEFORE deleting the order
        # Resolve target business: prefer order.business; else use ?business param or single user's business
        business_param = request.GET.get('business')
        user_businesses = request.user.businesses.all()
        target_business = order.business
        if not target_business:
            if business_param and business_param != 'all':
                try:
                    target_business = user_businesses.get(id=int(business_param))
                except Exception:
                    return Response({"error": "Invalid business id"}, status=status.HTTP_400_BAD_REQUEST)
            elif user_businesses.count() == 1:
                target_business = user_businesses.first()
            else:
                return Response({"error": "Ambiguous business. Provide ?business=<id>."}, status=status.HTTP_400_BAD_REQUEST)

        product = get_object_or_404(Product, product_name=order.product_name, business=target_business)
        
        # 🟢 CRITICAL CHANGE: Restore the product's stock
        product.current_stock += order.quantity
        product.save()

        # Delete the order
        order.delete()
        
        return Response(status=status.HTTP_204_NO_CONTENT)

    except ObjectDoesNotExist:
        return Response(
            {"error": "You do not have permission to delete this order, or it does not exist."},
            status=status.HTTP_403_FORBIDDEN
        )
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    

# -----------------------
# RETURNS
# -----------------------
@api_view(['GET', 'DELETE'])
@permission_classes([IsAuthenticated])
def returns_list(request):
    user_businesses = request.user.businesses.all()
    if not user_businesses:
        return Response([], status=status.HTTP_200_OK)
    
    # Support deletion via this endpoint to avoid 405s from client routing mismatches
    if request.method == 'DELETE':
        try:
            pk = request.query_params.get('id') or (request.data.get('id') if hasattr(request, 'data') else None)
            if not pk:
                return Response({"status": "error", "message": "Missing id for deletion"}, status=400)
            try:
                pk = int(pk)
            except Exception:
                return Response({"status": "error", "message": "Invalid id"}, status=400)

            # Find the return object first
            return_obj = get_object_or_404(Return, pk=pk)
            if return_obj.business and return_obj.business not in user_businesses:
                return Response({'status': 'error', 'message': 'Return does not belong to your business.'}, status=403)

            # Snapshot and delete first
            order = getattr(return_obj, 'order', None)
            product_name = getattr(return_obj, 'product_name', None) or (getattr(order, 'product_name', None) if order else None)
            quantity = getattr(return_obj, 'quantity', None) or (getattr(order, 'quantity', None) if order else 0)
            target_business = return_obj.business or (order.business if order else None)
            return_obj.delete()

            # Best-effort adjustments
            try:
                if order:
                    order.is_returned = False
                    order.save()
            except Exception:
                pass
            try:
                if product_name and target_business:
                    product = Product.objects.filter(product_name=product_name, business=target_business).first()
                    if product and quantity:
                        new_stock = (product.current_stock or 0) - int(quantity)
                        product.current_stock = new_stock if new_stock >= 0 else 0
                        product.save()
            except Exception:
                pass

            return Response({'status': 'success', 'message': 'Return successfully removed and order restored.'}, status=200)
        except ObjectDoesNotExist:
            return Response({'status': 'error', 'message': 'Return not found.'}, status=404)
        except Exception as e:
            return Response({'status': 'error', 'message': str(e)}, status=400)

    date_str = request.query_params.get('date')
    business_param = request.query_params.get('business')
    
    if business_param and business_param != 'all':
        try:
            b_id = int(business_param)
            if not user_businesses.filter(id=b_id).exists():
                return Response({"error": "Invalid business id"}, status=400)
            returns = Return.objects.filter(business_id=b_id)
        except Exception:
            return Response({"error": "Invalid business id"}, status=400)
    else:
        returns = Return.objects.filter(business__in=user_businesses)
    
    if date_str:
        try:
            filter_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            returns = returns.filter(date=filter_date)
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)

    serializer = ReturnSerializer(returns, many=True)
    return Response(serializer.data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def add_edit_return(request):
    try:
        user_businesses = request.user.businesses.all()
        if not user_businesses:
            return Response({'status': 'error', 'message': 'No business found for this user'}, status=400)
        
        order_id = request.data.get('order')

        # Fetch order by PK first; then enforce permission
        order = get_object_or_404(Order, pk=order_id)
        if order.business and order.business not in user_businesses:
            return Response({'status': 'error', 'message': 'Order does not belong to your business.'}, status=403)
        
        if order.is_returned:
            return Response({'status': 'error', 'message': 'This order has already been returned.'}, status=400)

        # Resolve target business for the return and product update
        business_param = request.GET.get('business')
        target_business = order.business
        if not target_business:
            if business_param and business_param != 'all':
                try:
                    target_business = user_businesses.get(id=int(business_param))
                except Exception:
                    return Response({'status': 'error', 'message': 'Invalid business id'}, status=400)
            elif user_businesses.count() == 1:
                target_business = user_businesses.first()
            else:
                return Response({'status': 'error', 'message': 'Ambiguous business. Provide ?business=<id>.'}, status=400)

        # 🟢 Get the product to update its stock
        product = get_object_or_404(Product, product_name=order.product_name, business=target_business)
        
        # 🟢 Increase the product's stock for the return
        product.current_stock += order.quantity
        product.save()

        # Inject the resolved order to prevent internal re-query by the serializer
        serializer = ReturnSerializer(
            data={k: v for k, v in request.data.items() if k != 'order'},
            context={'request': request, 'business_for_create': target_business, 'provided_order': order}
        )
        if serializer.is_valid():
            # Pass the resolved order and business explicitly to avoid lookup issues
            serializer.save(order=order, business=target_business)
            order.is_returned = True
            order.save()

            return Response({'status': 'success', 'data': serializer.data}, status=201)
        return Response({'status': 'error', 'errors': serializer.errors}, status=400)
    
    except ObjectDoesNotExist:
        return Response({'status': 'error', 'message': 'Order not found.'}, status=404)
    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=400)
    
    
@api_view(['DELETE', 'POST', 'GET', 'OPTIONS'])
@permission_classes([IsAuthenticated])
def delete_return(request, pk):
    try:
        business = request.user.businesses.first()
        if not business:
            return Response({'status': 'error', 'message': 'No business found for this user'}, status=400)

        ret = get_object_or_404(Return, pk=pk, business=business)

        ret.delete()
        return Response({'status': 'success', 'message': 'Return deleted'})
    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=400)

@api_view(['DELETE', 'POST', 'GET', 'OPTIONS'])
@permission_classes([IsAuthenticated])
def remove_return(request, pk):
    try:
        user_businesses = request.user.businesses.all()
        if not user_businesses:
            return Response({'status': 'error', 'message': 'No business found for this user'}, status=400)
        
        # 1. Find the return object first; then ensure it belongs to user's businesses
        return_obj = get_object_or_404(Return, pk=pk)
        if return_obj.business and return_obj.business not in user_businesses:
            return Response({'status': 'error', 'message': 'Return does not belong to your business.'}, status=403)
        
        # 2. Snapshot details we need from the return itself and delete the return first
        product_name = return_obj.product_name
        quantity = return_obj.quantity or 0
        # Try to get the related order for unmarking later (best-effort)
        try:
            related_order = return_obj.order
        except Exception:
            related_order = None
        
        # Determine target business
        business_param = request.GET.get('business')
        target_business = return_obj.business
        if not target_business:
            if business_param and business_param != 'all':
                try:
                    target_business = user_businesses.get(id=int(business_param))
                except Exception:
                    return Response({'status': 'error', 'message': 'Invalid business id'}, status=400)
            elif user_businesses.count() == 1:
                target_business = user_businesses.first()
            else:
                return Response({'status': 'error', 'message': 'Ambiguous business. Provide ?business=<id>.'}, status=400)

        # Delete return FIRST so removal doesn't fail due to downstream issues
        return_obj.delete()

        # 3. Best-effort: adjust stock if product exists
        try:
            if product_name and target_business:
                product = Product.objects.filter(product_name=product_name, business=target_business).first()
                if product and quantity:
                    new_stock = (product.current_stock or 0) - int(quantity)
                    product.current_stock = new_stock if new_stock >= 0 else 0
                    product.save()
        except Exception:
            pass

        # 4. Best-effort: mark related order as not returned so it shows in orders list
        try:
            if related_order:
                related_order.is_returned = False
                related_order.save()
        except Exception:
            pass
            
        return Response({'status': 'success', 'message': 'Return successfully removed and order restored.'}, status=200)

    except ObjectDoesNotExist:
        return Response({'status': 'error', 'message': 'Return not found.'}, status=404)
    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=400)    

@csrf_exempt
@require_POST
def update_product_stock(request):
    """
    Updates the stock of a product.
    This view is now ONLY called by other views internally.
    """
    return JsonResponse({'message': 'This endpoint is for internal use only.'}, status=403)


# --- Analysis: Sales Overview ---
# views.py

def _get_sales_overview_data(business, start_date, end_date):
    from .models import Product, Order, Return

    # Ensure start and end are proper date objects
    if isinstance(start_date, str):
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d").date()
        except ValueError:
            start = date.today() - timedelta(days=30)
    elif isinstance(start_date, date):
        start = start_date
    else:
        start = date.today() - timedelta(days=30)

    if isinstance(end_date, str):
        try:
            end = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            end = date.today()
    elif isinstance(end_date, date):
        end = end_date
    else:
        end = date.today()

    # Product lookups
    # Support single business or a list of business ids
    if isinstance(business, (list, tuple)):
        products = Product.objects.filter(business_id__in=business).only("product_name", "selling_price", "category")
        orders_qs = Order.objects.filter(business_id__in=business, date__gte=start, date__lte=end)
        returns_qs = Return.objects.filter(business_id__in=business, date__gte=start, date__lte=end)
    else:
        products = Product.objects.filter(business=business).only("product_name", "selling_price", "category")
        orders_qs = Order.objects.filter(business=business, date__gte=start, date__lte=end)
        returns_qs = Return.objects.filter(business=business, date__gte=start, date__lte=end)
    sp_map = {p.product_name: Decimal(str(p.selling_price)) for p in products}
    cat_map = {p.product_name: p.category for p in products}

    # orders_qs and returns_qs already set above

    # ---- Line Chart (sales trend)
    labels = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    line_bucket = OrderedDict((k, Decimal("0.00")) for k in labels)

    for o in orders_qs:
        line_bucket[o.date.isoformat()] += sp_map.get(o.product_name, Decimal("0.00")) * o.quantity
    for r in returns_qs:
        line_bucket[r.date.isoformat()] -= sp_map.get(r.product_name, Decimal("0.00")) * r.quantity

    line_data = [{"label": k, "sales": float(v)} for k, v in line_bucket.items()]

    # ---- Bar Chart (top products)
    product_sales = defaultdict(Decimal)
    for o in orders_qs:
        product_sales[o.product_name] += sp_map.get(o.product_name, Decimal("0.00")) * o.quantity
    for r in returns_qs:
        product_sales[r.product_name] -= sp_map.get(r.product_name, Decimal("0.00")) * r.quantity

    bar_data = [{"product": name, "sales": float(amount)} for name, amount in product_sales.items() if amount > 0]
    bar_data.sort(key=lambda x: x["sales"], reverse=True)
    bar_data = bar_data[:10]

    # ---- Pie Chart (categories)
    category_sales = defaultdict(Decimal)
    for o in orders_qs:
        cat = cat_map.get(o.product_name, "uncategorized")
        category_sales[cat] += sp_map.get(o.product_name, Decimal("0.00")) * o.quantity
    for r in returns_qs:
        cat = cat_map.get(r.product_name, "uncategorized")
        category_sales[cat] -= sp_map.get(r.product_name, Decimal("0.00")) * r.quantity

    pie_raw = [{"category": c, "value": float(v)} for c, v in category_sales.items() if v > 0]
    total = sum(x["value"] for x in pie_raw) or 1.0
    pie_data = [{"category": x["category"], "value": round(100.0 * x["value"] / total, 2)} for x in pie_raw]

    return {
        "line_data": line_data,
        "bar_data": bar_data,
        "pie_data": pie_data,
        "start": str(start),
        "end": str(end),
    }



@api_view(["GET"])
@permission_classes([IsAuthenticated])
def sales_overview(request):
    user = request.user
    user_businesses = list(user.businesses.values_list('id', flat=True)) if hasattr(user, 'businesses') else []
    if not user_businesses:
        return Response({"detail": "No business found."}, status=400)

    bparam = request.GET.get('business')
    if bparam and bparam != 'all':
        try:
            bid = int(bparam)
            if bid not in user_businesses:
                return Response({"detail": "Invalid business id"}, status=400)
            business = bid
            business_value = [bid]
        except Exception:
            return Response({"detail": "Invalid business id"}, status=400)
    else:
        business_value = user_businesses

    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")
    data = _get_sales_overview_data(business_value, start_date, end_date)
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def sales_overview_report(request):
    """
    Generates and downloads a CSV report for the specified date range.
    """
    user = request.user
    user_businesses = list(user.businesses.values_list('id', flat=True)) if hasattr(user, 'businesses') else []
    if not user_businesses:
        return Response({"detail": "No business found."}, status=400)

    bparam = request.GET.get('business')
    if bparam and bparam != 'all':
        try:
            bid = int(bparam)
            if bid not in user_businesses:
                return Response({"detail": "Invalid business id"}, status=400)
            business_value = [bid]
        except Exception:
            return Response({"detail": "Invalid business id"}, status=400)
    else:
        business_value = user_businesses
    
    # Explicitly handle date parsing and defaulting
    try:
        start_date_str = request.GET.get("start_date")
        end_date_str = request.GET.get("end_date")
        
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date() if start_date_str else date.today() - timedelta(days=29)
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date() if end_date_str else date.today()
    except (ValueError, TypeError) as e:
        # Return a more descriptive error to the client
        return Response({"detail": f"Invalid date format: {e}"}, status=400)

    # Call the helper function with the now-guaranteed valid date parameters
    data = _get_sales_overview_data(business_value, start_date, end_date)
    
    response = HttpResponse(content_type='text/csv')
    
    # Use the dates from the returned data for a more accurate filename
    filename = f'sales_overview_report_{data["start"]}_to_{data["end"]}.csv'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    
    # Write a CSV directive to ensure dates are not misinterpreted by Excel
    writer.writerow(['sep=,'])

    # Write Sales Trend data
    writer.writerow(["Sales Trend"])
    writer.writerow(["Date", "Sales (₹)"])
    for item in data['line_data']:
        writer.writerow([f"'{item['label']}", item['sales']])
    writer.writerow([])

    # Write Top Selling Products data
    writer.writerow(["Top Selling Products"])
    writer.writerow(["Product", "Sales (₹)"])
    for item in data['bar_data']:
        writer.writerow([item['product'], item['sales']])
    writer.writerow([])

    # Write Sales by Category data
    writer.writerow(["Sales by Category"])
    writer.writerow(["Category", "Percentage (%)"])
    for item in data['pie_data']:
        writer.writerow([item['category'], item['value']])

    return response





def add_months(d, m):
    y = d.year + (d.month - 1 + m) // 12
    mm = (d.month - 1 + m) % 12 + 1
    return date(y, mm, 1)

def _get_returns_analysis_data(business, rng, today, start_date=None, end_date=None):
    """
    Helper function to get all raw data for both charts and reports.
    """
    from .models import Product, Order, Return
    
    # If an explicit date range is provided and valid, use it. Otherwise fall back to rng logic
    if start_date and end_date and isinstance(start_date, date) and isinstance(end_date, date):
        start, end = start_date, end_date
        def make_label(d): return d.isoformat()
        labels = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    else:
        valid = {"weekly", "monthly", "yearly"}
        if rng not in valid:
            rng = "monthly"

        # --- Time window + label function
        if rng == "weekly":
            start = today - timedelta(days=6)
            end = today
            def make_label(d): return d.isoformat()
            labels = [(start + timedelta(days=i)).isoformat() for i in range(7)]
        elif rng == "monthly":
            start = today - timedelta(days=29)
            end = today
            def make_label(d): return d.isoformat()
            labels = [(start + timedelta(days=i)).isoformat() for i in range(30)]
        else: # yearly
            first_month = add_months(date(today.year, today.month, 1), -11)
            start = first_month
            end = today
            def make_label(d): return d.strftime("%Y-%m")
            labels = []
            for i in range(12):
                m = add_months(first_month, i)
                labels.append(m.strftime("%Y-%m"))

    # Load product prices for calculations
    products = Product.objects.filter(business=business).only("product_name", "selling_price")
    sp_map = {p.product_name: Decimal(str(p.selling_price)) for p in products}

    # Filter orders/returns in window
    orders_qs = Order.objects.filter(business=business, date__gte=start, date__lte=end)
    returns_qs = Return.objects.filter(business=business, date__gte=start, date__lte=end)

    # --- 1) Line: returns trend over time (by quantity)
    line_bucket = OrderedDict((k, 0) for k in labels)
    if rng in ("weekly", "monthly"):
        for r in returns_qs:
            key = make_label(r.date)
            line_bucket[key] = line_bucket.get(key, 0) + r.quantity
    else: # yearly
        for r in returns_qs:
            key = make_label(date(r.date.year, r.date.month, 1))
            line_bucket[key] = line_bucket.get(key, 0) + r.quantity

    line_data = [{"label": k, "returns": v} for k, v in line_bucket.items()]

    # --- 2) Bar: most returned products (by quantity)
    bar_data_raw = returns_qs.values('product_name').annotate(
        total_returns=Sum('quantity', output_field=DecimalField())
    ).order_by('-total_returns')[:5]

    bar_data = [{"product": item['product_name'], "returns": float(item['total_returns'])} for item in bar_data_raw]


    # --- 3) Donut: returns vs. sales (by value)
    total_sales = Decimal("0.00")
    for o in orders_qs:
        total_sales += sp_map.get(o.product_name, Decimal("0.00")) * o.quantity

    total_returns = Decimal("0.00")
    for r in returns_qs:
        total_returns += sp_map.get(r.product_name, Decimal("0.00")) * r.quantity
    
    donut_data = [
        {"name": "Sales", "value": float(total_sales)},
        {"name": "Returns", "value": float(total_returns)}
    ]

    return {
        "line_data": line_data,
        "bar_data": bar_data,
        "donut_data": donut_data,
        "range": rng,
    }

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def returns_analysis(request):
    """
    Returns returns analysis data for charts.
    """
    user = request.user
    user_businesses = list(user.businesses.values_list('id', flat=True)) if hasattr(user, 'businesses') else []
    if not user_businesses:
        return Response({"detail": "No business found."}, status=400)

    bparam = request.GET.get('business')
    if bparam and bparam != 'all':
        try:
            bid = int(bparam)
            if bid not in user_businesses:
                return Response({"detail": "Invalid business id"}, status=400)
            business = get_object_or_404(Business, id=bid, owner=user)
        except Exception:
            return Response({"detail": "Invalid business id"}, status=400)
    else:
        business = list(user_businesses)

    rng = request.GET.get("range", "monthly").lower()
    # Optional date range support
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")
    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            if start_date > end_date:
                return Response({"detail": "start_date cannot be after end_date"}, status=400)
        except Exception:
            return Response({"detail": "Invalid date format. Use YYYY-MM-DD."}, status=400)
        data = _get_returns_analysis_data(business, rng, date.today(), start_date, end_date)
    else:
        data = _get_returns_analysis_data(business, rng, date.today())
    return Response(data)

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def returns_analysis_report(request):
    """
    Generates and downloads a CSV report for returns analysis.
    """
    user = request.user
    user_businesses = list(user.businesses.values_list('id', flat=True)) if hasattr(user, 'businesses') else []
    if not user_businesses:
        return Response({"detail": "No business found."}, status=400)
    bparam = request.GET.get('business')
    if bparam and bparam != 'all':
        try:
            bid = int(bparam)
            if bid not in user_businesses:
                return Response({"detail": "Invalid business id"}, status=400)
            business = get_object_or_404(Business, id=bid, owner=user)
        except Exception:
            return Response({"detail": "Invalid business id"}, status=400)
    else:
        business = list(user_businesses)

    rng = request.GET.get("range", "monthly").lower()
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")
    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            if start_date > end_date:
                return Response({"detail": "start_date cannot be after end_date"}, status=400)
        except Exception:
            return Response({"detail": "Invalid date format. Use YYYY-MM-DD."}, status=400)
        data = _get_returns_analysis_data(business, rng, date.today(), start_date, end_date)
    else:
        data = _get_returns_analysis_data(business, rng, date.today())
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="returns_analysis_report_{rng}.csv"'

    writer = csv.writer(response)

    # Write CSV directive and headers for the first section
    writer.writerow(['sep=,'])
    writer.writerow(["Returns Trend"])
    writer.writerow(["Date", "Returns (Quantity)"])
    for item in data['line_data']:
        writer.writerow([f"'{item['label']}", item['returns']])
    writer.writerow([])

    # Write headers for the second section
    writer.writerow(["Most Returned Products"])
    writer.writerow(["Product", "Total Returns (Quantity)"])
    for item in data['bar_data']:
        writer.writerow([item['product'], item['returns']])
    writer.writerow([])

    # Write headers for the third section
    writer.writerow(["Returns vs. Sales (by value)"])
    writer.writerow(["Category", "Value"])
    for item in data['donut_data']:
        writer.writerow([item['name'], item['value']])

    return response


def _get_revenue_profit_analysis_data(business, rng, today, start_date=None, end_date=None):
    """
    Helper function to get all raw data for both charts and reports.
    """
    from .models import Product, Order, Return

    if start_date and end_date and isinstance(start_date, date) and isinstance(end_date, date):
        start, end = start_date, end_date
        def make_label(d): return d.isoformat()
        labels = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    else:
        valid = {"weekly", "monthly", "yearly"}
        if rng not in valid:
            rng = "monthly"

        # --- Time window + label function
        if rng == "weekly":
            start = today - timedelta(days=6)
            end = today
            def make_label(d): return d.isoformat()
            labels = [(start + timedelta(days=i)).isoformat() for i in range(7)]
        elif rng == "monthly":
            start = today - timedelta(days=29)
            end = today
            def make_label(d): return d.isoformat()
            labels = [(start + timedelta(days=i)).isoformat() for i in range(30)]
        else: # yearly
            first_month = add_months(date(today.year, today.month, 1), -11)
            start = first_month
            end = today
            def make_label(d): return d.strftime("%Y-%m")
            labels = []
            for i in range(12):
                m = add_months(first_month, i)
                labels.append(m.strftime("%Y-%m"))

    # Load product details for calculations (single or multiple businesses)
    if isinstance(business, (list, tuple)):
        products = Product.objects.filter(business_id__in=business).only("product_name", "selling_price", "price", "category")
        orders_qs = Order.objects.filter(business_id__in=business, date__gte=start, date__lte=end)
        returns_qs = Return.objects.filter(business_id__in=business, date__gte=start, date__lte=end)
    else:
        products = Product.objects.filter(business=business).only("product_name", "selling_price", "price", "category")
        orders_qs = Order.objects.filter(business=business, date__gte=start, date__lte=end)
        returns_qs = Return.objects.filter(business=business, date__gte=start, date__lte=end)

    sp_map = {p.product_name: Decimal(str(p.selling_price)) for p in products}
    cp_map = {p.product_name: Decimal(str(p.price)) for p in products}
    cat_map = {p.product_name: p.category for p in products}

    # --- 1) Bar: Revenue vs Cost per product
    product_data = defaultdict(lambda: {"revenue": Decimal("0.00"), "cost": Decimal("0.00")})

    for order in orders_qs:
        product_name = order.product_name
        if order.quantity and product_name in sp_map and product_name in cp_map:
            revenue = sp_map[product_name] * Decimal(str(order.quantity))
            cost = cp_map[product_name] * Decimal(str(order.quantity))
            product_data[product_name]["revenue"] += revenue
            product_data[product_name]["cost"] += cost
    
    for ret in returns_qs:
        product_name = ret.product_name
        if ret.quantity and product_name in sp_map:
            revenue_reduction = sp_map[product_name] * Decimal(str(ret.quantity))
            product_data[product_name]["revenue"] -= revenue_reduction

    revenue_cost_data = [{"product_name": k, "revenue": float(v["revenue"]), "cost": float(v["cost"])} 
                          for k, v in product_data.items() if v["revenue"] > 0]
    revenue_cost_data.sort(key=lambda x: x["revenue"], reverse=True)


    # --- 2) Line: Revenue growth over time
    revenue_growth_bucket = OrderedDict((k, Decimal("0.00")) for k in labels)
    for order in orders_qs:
        product_name = order.product_name
        if order.quantity and product_name in sp_map:
            key = make_label(order.date) if rng in ("weekly", "monthly") else make_label(date(order.date.year, order.date.month, 1))
            revenue_growth_bucket[key] += sp_map[product_name] * Decimal(str(order.quantity))
    
    for ret in returns_qs:
        product_name = ret.product_name
        if ret.quantity and product_name in sp_map:
            key = make_label(ret.date) if rng in ("weekly", "monthly") else make_label(date(ret.date.year, ret.date.month, 1))
            revenue_growth_bucket[key] -= sp_map[product_name] * Decimal(str(ret.quantity))

    revenue_growth_data = [{"label": k, "revenue": float(v)} for k, v in revenue_growth_bucket.items()]

    
    # --- 3) Stacked Bar: Profit contribution by category
    profit_by_category_and_product = defaultdict(lambda: defaultdict(Decimal))
    for order in orders_qs:
        product_name = order.product_name
        if order.quantity and product_name in sp_map and product_name in cp_map and product_name in cat_map:
            category = cat_map[product_name]
            profit = (sp_map[product_name] - cp_map[product_name]) * Decimal(str(order.quantity))
            profit_by_category_and_product[category][product_name] += profit

    for ret in returns_qs:
        product_name = ret.product_name
        if ret.quantity and product_name in sp_map and product_name in cp_map and product_name in cat_map:
            category = cat_map[product_name]
            profit_reduction = (sp_map[product_name] - cp_map[product_name]) * Decimal(str(ret.quantity))
            profit_by_category_and_product[category][product_name] -= profit_reduction
    
    profit_category_data = []
    for category, products_data in profit_by_category_and_product.items():
        if any(profit > 0 for profit in products_data.values()):
            data_row = {"category": category}
            for product, profit in products_data.items():
                if profit > 0:
                    data_row[product] = float(profit)
            profit_category_data.append(data_row)
    
    return {
        "revenue_cost_data": revenue_cost_data,
        "revenue_growth_data": revenue_growth_data,
        "profit_category_data": profit_category_data,
        "range": rng,
    }

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def revenue_profit_analysis(request):
    """
    Returns returns analysis data for charts.
    """
    user = request.user
    user_businesses = list(user.businesses.values_list('id', flat=True)) if hasattr(user, 'businesses') else []
    if not user_businesses:
        return Response({"detail": "No business found."}, status=400)
    bparam = request.GET.get('business')
    if bparam and bparam != 'all':
        try:
            bid = int(bparam)
            if bid not in user_businesses:
                return Response({"detail": "Invalid business id"}, status=400)
            business = get_object_or_404(Business, id=bid, owner=user)
        except Exception:
            return Response({"detail": "Invalid business id"}, status=400)
    else:
        business = list(user_businesses)
    
    rng = request.GET.get("range", "monthly").lower()
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")
    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            if start_date > end_date:
                return Response({"detail": "start_date cannot be after end_date"}, status=400)
        except Exception:
            return Response({"detail": "Invalid date format. Use YYYY-MM-DD."}, status=400)
        data = _get_revenue_profit_analysis_data(business, rng, date.today(), start_date, end_date)
    else:
        data = _get_revenue_profit_analysis_data(business, rng, date.today())
    return Response(data)

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def revenue_profit_analysis_report(request):
    """
    Generates and downloads a CSV report for revenue and profit analysis.
    """
    user = request.user
    user_businesses = list(user.businesses.values_list('id', flat=True)) if hasattr(user, 'businesses') else []
    if not user_businesses:
        return Response({"detail": "No business found."}, status=400)
    bparam = request.GET.get('business')
    if bparam and bparam != 'all':
        try:
            bid = int(bparam)
            if bid not in user_businesses:
                return Response({"detail": "Invalid business id"}, status=400)
            business = get_object_or_404(Business, id=bid, owner=user)
        except Exception:
            return Response({"detail": "Invalid business id"}, status=400)
    else:
        business = list(user_businesses)

    rng = request.GET.get("range", "monthly").lower()
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")
    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            if start_date > end_date:
                return Response({"detail": "start_date cannot be after end_date"}, status=400)
        except Exception:
            return Response({"detail": "Invalid date format. Use YYYY-MM-DD."}, status=400)
        data = _get_revenue_profit_analysis_data(business, rng, date.today(), start_date, end_date)
    else:
        data = _get_revenue_profit_analysis_data(business, rng, date.today())
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="revenue_profit_analysis_report_{rng}.csv"'

    writer = csv.writer(response)

    # Write CSV directive and headers for the first section
    writer.writerow(['sep=,'])
    writer.writerow(["Revenue vs. Cost"])
    writer.writerow(["Product Name", "Revenue", "Cost"])
    for item in data['revenue_cost_data']:
        writer.writerow([item['product_name'], item['revenue'], item['cost']])
    writer.writerow([])

    # Write headers for the second section
    writer.writerow(["Revenue Growth Trend"])
    writer.writerow(["Date", "Revenue (₹)"])
    for item in data['revenue_growth_data']:
        writer.writerow([f"'{item['label']}", item['revenue']])
    writer.writerow([])

    # Write headers for the third section
    writer.writerow(["Profit by Category and Product"])
    writer.writerow(["Category", "Product", "Profit (₹)"])
    for category_data in data['profit_category_data']:
        category = category_data['category']
        # Dynamically get product keys from the dictionary
        products = [k for k in category_data if k != 'category']
        for product in products:
            profit = category_data[product]
            writer.writerow([category, product, profit])

    return response


def _get_inventory_analysis_data(business, today, start_date: date | None = None, end_date: date | None = None):
    """
    Helper function to get all raw data for both charts and reports.
    """
    from .models import Product, Order, Return

    # --- 1) Low Stock Products
    # Filter for products where current_stock is at or below min_stock
    low_stock_products_filter = {"business_id__in": business} if isinstance(business, (list, tuple)) else {"business": business}
    low_stock_products_qs = Product.objects.filter(
        **low_stock_products_filter,
        current_stock__lte=F('min_stock')
    ).order_by('current_stock')

    low_stock_products = [{"product_name": p.product_name, "current_stock": p.current_stock}
                          for p in low_stock_products_qs]
    
    # --- 2) Current Inventory Value
    # Calculate total value of all products in stock
    products_qs = Product.objects.filter(**({"business_id__in": business} if isinstance(business, (list, tuple)) else {"business": business})).only("current_stock", "price")
    total_inventory_value = Decimal("0.00")
    for p in products_qs:
        total_inventory_value += Decimal(str(p.current_stock)) * Decimal(str(p.price))
    
    # --- 3) Stock Movement Trend
    # If a date range is provided, compute daily net movement within the range.
    # Otherwise, provide monthly cumulative stock trend for the last 12 months (existing behavior).
    if start_date and end_date:
        # Daily net movement: returns add stock, orders subtract stock
        num_days = (end_date - start_date).days + 1
        labels = [(start_date + timedelta(days=i)).isoformat() for i in range(max(num_days, 0))]
        daily_bucket = OrderedDict((k, 0) for k in labels)

        orders = Order.objects.filter(**({"business_id__in": business} if isinstance(business, (list, tuple)) else {"business": business}), date__gte=start_date, date__lte=end_date)
        returns = Return.objects.filter(**({"business_id__in": business} if isinstance(business, (list, tuple)) else {"business": business}), date__gte=start_date, date__lte=end_date)

        for order in orders:
            key = order.date.isoformat()
            if key in daily_bucket:
                daily_bucket[key] -= order.quantity
        for ret in returns:
            key = ret.date.isoformat()
            if key in daily_bucket:
                daily_bucket[key] += ret.quantity

        stock_movement_data = [{"label": k, "stock": v} for k, v in daily_bucket.items()]
    else:
        # Default: last 12 months cumulative stock snapshot
        start = add_months(date(today.year, today.month, 1), -11)

        all_products = Product.objects.filter(**({"business_id__in": business} if isinstance(business, (list, tuple)) else {"business": business})).only("product_name", "current_stock")
        stock_levels = {p.product_name: p.current_stock for p in all_products}

        stock_trend = OrderedDict()

        for i in range(12):
            month_start = add_months(start, i)
            month_end = add_months(start, i + 1) - timedelta(days=1)
            month_label = month_start.strftime("%Y-%m")

            orders = Order.objects.filter(**({"business_id__in": business} if isinstance(business, (list, tuple)) else {"business": business}), date__range=(month_start, month_end))
            returns = Return.objects.filter(**({"business_id__in": business} if isinstance(business, (list, tuple)) else {"business": business}), date__range=(month_start, month_end))

            for order in orders:
                if order.product_name in stock_levels:
                    stock_levels[order.product_name] -= order.quantity

            for ret in returns:
                if ret.product_name in stock_levels:
                    stock_levels[ret.product_name] += ret.quantity

            stock_trend[month_label] = sum(stock_levels.values())

        stock_movement_data = [{"label": k, "stock": v} for k, v in stock_trend.items()]

    return {
        "low_stock_products": low_stock_products,
        "inventory_value": float(total_inventory_value),
        "stock_movement_data": stock_movement_data,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def inventory_analysis(request):
    """
    Returns inventory analysis data for charts.
    """
    user = request.user
    user_businesses = list(user.businesses.values_list('id', flat=True)) if hasattr(user, 'businesses') else []
    if not user_businesses:
        return Response({"detail": "No business found."}, status=400)

    bparam = request.GET.get('business')
    if bparam and bparam != 'all':
        try:
            bid = int(bparam)
            if bid not in user_businesses:
                return Response({"detail": "Invalid business id"}, status=400)
            business_value = [bid]
        except Exception:
            return Response({"detail": "Invalid business id"}, status=400)
    else:
        business_value = user_businesses

    # Optional date range support
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")
    start_date = end_date = None
    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            if start_date > end_date:
                return Response({"detail": "start_date cannot be after end_date"}, status=400)
        except Exception:
            return Response({"detail": "Invalid date format. Use YYYY-MM-DD."}, status=400)

    data = _get_inventory_analysis_data(business_value, date.today(), start_date, end_date)

    return Response({
        "low_stock_products": data["low_stock_products"],
        "inventory_value": data["inventory_value"],
        "stock_movement_data": data["stock_movement_data"]
    })

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def inventory_analysis_report(request):
    """
    Generates and downloads a CSV report for inventory analysis.
    """
    user = request.user
    user_businesses = list(user.businesses.values_list('id', flat=True)) if hasattr(user, 'businesses') else []
    if not user_businesses:
        return Response({"detail": "No business found."}, status=400)

    bparam = request.GET.get('business')
    if bparam and bparam != 'all':
        try:
            bid = int(bparam)
            if bid not in user_businesses:
                return Response({"detail": "Invalid business id"}, status=400)
            business_value = [bid]
        except Exception:
            return Response({"detail": "Invalid business id"}, status=400)
    else:
        business_value = user_businesses

    # Optional date range support for the movement section
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")
    start_date = end_date = None
    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            if start_date > end_date:
                return Response({"detail": "start_date cannot be after end_date"}, status=400)
        except Exception:
            return Response({"detail": "Invalid date format. Use YYYY-MM-DD."}, status=400)

    data = _get_inventory_analysis_data(business_value, date.today(), start_date, end_date)
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="inventory_analysis_report.csv"'

    writer = csv.writer(response)

    # Write CSV directive and headers for the first section
    writer.writerow(['sep=,'])
    writer.writerow(["Low Stock Products"])
    writer.writerow(["Product Name", "Current Stock"])
    for item in data['low_stock_products']:
        writer.writerow([item['product_name'], item['current_stock']])
    writer.writerow([])

    # Write headers for the second section
    writer.writerow(["Current Inventory Value"])
    writer.writerow(["Total Value (₹)"])
    writer.writerow([data['inventory_value']])
    writer.writerow([])

    # Write headers for the third section
    writer.writerow(["Stock Movement Trend"])
    writer.writerow(["Date", "Total Stock (Units)"])
    for item in data['stock_movement_data']:
        writer.writerow([f"'{item['label']}", item['stock']])

    return response


def _get_customer_sales_analysis_data(business, rng, today, start_date=None, end_date=None):
    """
    Helper function to get all raw data for both charts and reports.
    """
    from .models import Product, Order
    
    if start_date and end_date and isinstance(start_date, date) and isinstance(end_date, date):
        start, end = start_date, end_date
        def make_label(d): return d.isoformat()
        labels = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    else:
        valid = {"weekly", "monthly", "yearly"}
        if rng not in valid:
            rng = "monthly"

        # --- Time window + label function
        if rng == "weekly":
            start = today - timedelta(days=6)
            end = today
            def make_label(d): return d.isoformat()
            labels = [(start + timedelta(days=i)).isoformat() for i in range(7)]
        elif rng == "monthly":
            start = today - timedelta(days=29)
            end = today
            def make_label(d): return d.isoformat()
            labels = [(start + timedelta(days=i)).isoformat() for i in range(30)]
        else: # yearly
            first_month = add_months(date(today.year, today.month, 1), -11)
            start = first_month
            end = today
            def make_label(d): return d.strftime("%Y-%m")
            labels = []
            for i in range(12):
                m = add_months(first_month, i)
                labels.append(m.strftime("%Y-%m"))
            
    # Load product prices for revenue calculations
    if isinstance(business, (list, tuple)):
        products = Product.objects.filter(business_id__in=business).only("product_name", "selling_price")
        orders_qs = Order.objects.filter(business_id__in=business, date__gte=start, date__lte=end)
    else:
        products = Product.objects.filter(business=business).only("product_name", "selling_price")
        orders_qs = Order.objects.filter(business=business, date__gte=start, date__lte=end)
    sp_map = {p.product_name: Decimal(str(p.selling_price)) for p in products}

    # Filter orders in window
    # orders_qs already set above
    
    # --- 1) Top Customers
    customer_revenue = defaultdict(Decimal)
    for order in orders_qs:
        if order.quantity and order.product_name in sp_map:
            revenue = sp_map[order.product_name] * Decimal(str(order.quantity))
            customer_revenue[order.customer_name] += revenue
    
    sorted_customers = sorted(customer_revenue.items(), key=lambda item: item[1], reverse=True)[:5]
    top_customers = [{"customer_name": k, "total_revenue": float(v)} for k, v in sorted_customers]

    # --- 2) Top Selling Products
    product_sales = defaultdict(int)
    for order in orders_qs:
        if order.quantity:
            product_sales[order.product_name] += order.quantity
            
    sorted_products = sorted(product_sales.items(), key=lambda item: item[1], reverse=True)[:5]
    top_selling_products = [{"product_name": k, "total_quantity": v} for k, v in sorted_products]

    # --- 3) Sales Trend over time
    sales_trend_bucket = OrderedDict((k, Decimal("0.00")) for k in labels)
    for order in orders_qs:
        if order.quantity and order.product_name in sp_map:
            key = make_label(order.date) if rng in ("weekly", "monthly") else make_label(date(order.date.year, order.date.month, 1))
            revenue = sp_map[order.product_name] * Decimal(str(order.quantity))
            if key in sales_trend_bucket:
                sales_trend_bucket[key] += revenue
    
    sales_trend_data = [{"label": k, "sales": float(v)} for k, v in sales_trend_bucket.items()]

    return {
        "top_customers": top_customers,
        "top_selling_products": top_selling_products,
        "sales_trend_data": sales_trend_data,
        "range": rng,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def customer_sales_analysis(request):
    """
    Returns customer sales analysis data for charts.
    """
    user = request.user
    user_businesses = list(user.businesses.values_list('id', flat=True)) if hasattr(user, 'businesses') else []
    if not user_businesses:
        return Response({"detail": "No business found."}, status=400)

    bparam = request.GET.get('business')
    if bparam and bparam != 'all':
        try:
            bid = int(bparam)
            if bid not in user_businesses:
                return Response({"detail": "Invalid business id"}, status=400)
            business_value = [bid]
        except Exception:
            return Response({"detail": "Invalid business id"}, status=400)
    else:
        business_value = user_businesses

    rng = request.GET.get("range", "monthly").lower()
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")
    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            if start_date > end_date:
                return Response({"detail": "start_date cannot be after end_date"}, status=400)
        except Exception:
            return Response({"detail": "Invalid date format. Use YYYY-MM-DD."}, status=400)
        data = _get_customer_sales_analysis_data(business_value, rng, date.today(), start_date, end_date)
    else:
        data = _get_customer_sales_analysis_data(business_value, rng, date.today())
    
    return Response({
        "top_customers": data["top_customers"],
        "top_selling_products": data["top_selling_products"],
        "sales_trend_data": data["sales_trend_data"],
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def customer_sales_analysis_report(request):
    """
    Generates and downloads a CSV report for customer sales analysis.
    """
    user = request.user
    user_businesses = list(user.businesses.values_list('id', flat=True)) if hasattr(user, 'businesses') else []
    if not user_businesses:
        return Response({"detail": "No business found."}, status=400)

    bparam = request.GET.get('business')
    if bparam and bparam != 'all':
        try:
            bid = int(bparam)
            if bid not in user_businesses:
                return Response({"detail": "Invalid business id"}, status=400)
            business_value = [bid]
        except Exception:
            return Response({"detail": "Invalid business id"}, status=400)
    else:
        business_value = user_businesses

    rng = request.GET.get("range", "monthly").lower()
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")
    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            if start_date > end_date:
                return Response({"detail": "start_date cannot be after end_date"}, status=400)
        except Exception:
            return Response({"detail": "Invalid date format. Use YYYY-MM-DD."}, status=400)
        data = _get_customer_sales_analysis_data(business_value, rng, date.today(), start_date, end_date)
    else:
        data = _get_customer_sales_analysis_data(business_value, rng, date.today())
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="customer_sales_report_{rng}.csv"'

    writer = csv.writer(response)

    # Write CSV directive and headers for the first section
    writer.writerow(['sep=,'])
    writer.writerow(["Top Customers by Revenue"])
    writer.writerow(["Customer Name", "Total Revenue (₹)"])
    for item in data['top_customers']:
        writer.writerow([item['customer_name'], item['total_revenue']])
    writer.writerow([])

    # Write headers for the second section
    writer.writerow(["Top Selling Products"])
    writer.writerow(["Product Name", "Total Quantity Sold"])
    for item in data['top_selling_products']:
        writer.writerow([item['product_name'], item['total_quantity']])
    writer.writerow([])

    # Write headers for the third section
    writer.writerow(["Sales Trend"])
    writer.writerow(["Date", "Total Sales (₹)"])
    for item in data['sales_trend_data']:
        writer.writerow([f"'{item['label']}", item['sales']])

    return response



# ------------------------- Daily Sales -------------------------
def _get_daily_sales(business):
    """
    Fetch daily sales data and handle nulls.
    Returns DataFrame with columns ['date', 'sales'].
    """
    try:
        orders_qs = Order.objects.filter(business=business, is_returned=False).order_by('date')
        products_qs = Product.objects.filter(business=business)

        if not orders_qs.exists() or not products_qs.exists():
            return pd.DataFrame(), "Not enough data to create a forecast. Please add sales and products."

        orders_df = pd.DataFrame(list(orders_qs.values('date', 'product_name', 'quantity')))
        products_df = pd.DataFrame(list(products_qs.values('product_name', 'selling_price')))

        orders_df = pd.merge(orders_df, products_df, on='product_name', how='left')
        orders_df['total_sales'] = orders_df['quantity'] * orders_df['selling_price'].fillna(0)

        daily_sales_df = orders_df.groupby('date')['total_sales'].sum().reset_index()
        daily_sales_df.columns = ['date', 'sales']
        daily_sales_df['date'] = pd.to_datetime(daily_sales_df['date'])
        daily_sales_df = daily_sales_df.sort_values('date')
        daily_sales_df['sales'] = daily_sales_df['sales'].fillna(0)

        return daily_sales_df, None

    except Exception as e:
        return pd.DataFrame(), f"An error occurred while fetching data: {e}"


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def sales_forecast_analysis(request):
    user_business = request.user.businesses.first()
    if not user_business:
        return Response({"forecast_data": [], "message": "No business profile found."}, status=400)

    daily_sales_df, error_message = _get_daily_sales(user_business)
    if error_message:
        return Response({"forecast_data": [], "message": error_message}, status=200)

    if daily_sales_df.shape[0] < 30:
        return Response({
            "forecast_data": daily_sales_df.to_dict("records"),
            "message": "Not enough data for forecast (minimum 30 days required)."
        }, status=200)

    start_date = daily_sales_df["date"].iloc[0]
    daily_sales_df["days_since_start"] = (daily_sales_df["date"] - start_date).dt.days
    X = daily_sales_df[["days_since_start"]]
    y = daily_sales_df["sales"]

    poly_degree = 2
    last_date = daily_sales_df["date"].iloc[-1]

    try:
        # Try to load saved model
        model_obj = SalesForecastModel.objects.get(business=user_business)
        if model_obj.polynomial_degree != poly_degree:
            raise SalesForecastModel.DoesNotExist

        coefficients = np.array(model_obj.coefficients)
        intercept = model_obj.intercept
        poly_features = PolynomialFeatures(degree=poly_degree)
        poly_features.fit(X)

        future_dates = pd.date_range(start=last_date + timedelta(days=1), periods=30)
        future_days = (future_dates - start_date).days.values.reshape(-1, 1)
        forecasted_sales = np.dot(poly_features.transform(future_days), coefficients) + intercept
        message = "Forecast generated using saved model."

    except SalesForecastModel.DoesNotExist:
        # Train new model
        model = Pipeline([
            ("poly_features", PolynomialFeatures(degree=poly_degree)),
            ("linear_regression", LinearRegression())
        ])
        model.fit(X, y)

        future_dates = pd.date_range(start=last_date + timedelta(days=1), periods=30)
        future_days = (future_dates - start_date).days.values.reshape(-1, 1)
        forecasted_sales = model.predict(future_days)

        SalesForecastModel.objects.update_or_create(
            business=user_business,
            defaults={
                "coefficients": list(model.named_steps["linear_regression"].coef_),
                "intercept": float(model.named_steps["linear_regression"].intercept_),
                "polynomial_degree": poly_degree
            }
        )
        message = "New forecast model trained and saved."

    # Clean forecasted values
    forecasted_sales = np.nan_to_num(forecasted_sales, nan=0.0, posinf=0.0, neginf=0.0)
    forecasted_sales[forecasted_sales < 0] = 0.0

    forecast_df = pd.DataFrame({
        "date": pd.date_range(start=last_date + timedelta(days=1), periods=30),
        "sales": forecasted_sales,
        "type": "Forecast"
    })

    historical_df = daily_sales_df[["date", "sales"]].copy()
    historical_df["type"] = "Historical"

    combined = pd.concat([historical_df, forecast_df])
    combined["date"] = combined["date"].dt.strftime("%Y-%m-%d")
    combined["sales"] = combined["sales"].round(2).astype(float)

    return Response({"forecast_data": combined.to_dict("records"), "message": message})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def retrain_forecast_model(request):
    user_business = request.user.businesses.first()
    if not user_business:
        return Response({"message": "No business profile found."}, status=400)

    # Delete old model
    SalesForecastModel.objects.filter(business=user_business).delete()
    
    # Return a success message
    return Response({"message": "Model retraining triggered successfully."})


@csrf_exempt
def forecast_all_products(request):
    """
    API endpoint: returns forecast for all products
    """
    try:
        products = Product.objects.all()
        response_data = []

        for product in products:
            today = date.today()
            forecast = []
            for i in range(7):
                forecast.append({
                    "date": (today + timedelta(days=i)).strftime("%Y-%m-%d"),
                    "predicted_demand": random.randint(3, 15),
                })

            avg_daily_demand = sum(f["predicted_demand"] for f in forecast) // 7
            days_until_restock = (
                product.current_stock // avg_daily_demand if avg_daily_demand > 0 else 0
            )

            if days_until_restock <= 2:
                risk = "high"
                recommendation = "Urgent: Reorder immediately. Stock will run out soon."
            elif days_until_restock <= 7:
                risk = "medium"
                recommendation = "Monitor closely. Consider placing order within next week."
            else:
                risk = "low"
                recommendation = "Stock levels are healthy. Continue monitoring trends."

            prediction_confidence = random.randint(75, 95)

            response_data.append({
                "product_name": product.product_name,
                "current_stock": product.current_stock,
                "prediction_confidence": prediction_confidence,
                "forecast": forecast,
                "avg_daily_demand": avg_daily_demand,
                "days_until_restock": days_until_restock,
                "recommendation": recommendation,
                "risk": risk,
            })

        return JsonResponse(response_data, safe=False)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
    
