from django.conf.urls import include, url
from django.urls import path, re_path
from rest_framework.routers import DefaultRouter

router = DefaultRouter(trailing_slash=False)
router.register(r'articles', ArticleViewSet)

urlpatterns = [
    url(r'^admin/', admin.site.urls),
    url(r'^', include(router.urls)),
    url(r'^articles/feed/?$', ArticlesFeedAPIView.as_view()),
    url(r'^articles/(?P<article_slug>[-\w]+)/comments/(?P<comment_pk>[\d]+)/?$',
        CommentsDestroyAPIView.as_view()),
    path('health/', views.health),
    path('users/<int:pk>/', views.user_detail, name='user-detail'),
    re_path(r'^tags/?$', TagListAPIView.as_view()),
    path('api/', include('blog.urls')),
]
