# -*- coding: utf-8 -*-
#
from django.db.models.signals import m2m_changed, pre_delete
from django.dispatch import receiver

from perms.tasks import create_rebuild_user_tree_task, \
    create_rebuild_user_tree_task_by_related_nodes_or_assets
from users.models import User
from assets.models import Asset
from orgs.utils import current_org
from common.utils import get_logger
from common.exceptions import M2MReverseNotAllowed
from common.const.signals import POST_ADD, POST_REMOVE, POST_CLEAR
from perms.models import AssetPermission
from perms.utils.asset.user_permission import UserGrantedTreeRefreshController


logger = get_logger(__file__)


@receiver(m2m_changed, sender=User.groups.through)
def on_user_groups_change(sender, instance, action, reverse, pk_set, **kwargs):
    if action.startswith('post'):
        if reverse:
            group_ids = [instance.id]
            user_ids = pk_set
        else:
            group_ids = pk_set
            user_ids = [instance.id]

        exists = AssetPermission.user_groups.through.objects.filter(usergroup_id__in=group_ids).exists()
        if exists:
            org_ids = [current_org.id]
            UserGrantedTreeRefreshController.add_need_refresh_orgs_for_users(org_ids, user_ids)


@receiver([pre_delete], sender=AssetPermission)
def on_asset_permission_delete(sender, instance, **kwargs):
    # 授权删除之前，查出所有相关用户
    create_rebuild_user_tree_task_by_asset_perm(instance)


def create_rebuild_user_tree_task_by_asset_perm(asset_perm: AssetPermission):
    user_ids = set()

    user_ids.update(AssetPermission.users.through.objects.filter(
        assetpermission_id=asset_perm.id
    ).values_list('user_id', flat=True).distinct())

    group_ids = list(AssetPermission.user_groups.through.objects.filter(
        assetpermission_id=asset_perm.id
    ).values_list('usergroup_id', flat=True).distinct())

    user_ids.update(User.groups.through.objects.filter(
        usergroup_id__in=group_ids
    ).values_list('user_id', flat=True).distinct())

    UserGrantedTreeRefreshController.add_need_refresh_orgs_for_users(
        [current_org.id], user_ids
    )


def need_rebuild_mapping_node(action):
    return action in (POST_REMOVE, POST_ADD, POST_CLEAR)


@receiver(m2m_changed, sender=AssetPermission.nodes.through)
def on_permission_nodes_changed(sender, instance, action, reverse, pk_set, model, **kwargs):
    if reverse:
        raise M2MReverseNotAllowed

    if need_rebuild_mapping_node(action):
        create_rebuild_user_tree_task_by_asset_perm(instance)


@receiver(m2m_changed, sender=AssetPermission.assets.through)
def on_permission_assets_changed(sender, instance, action, reverse, pk_set, model, **kwargs):
    if reverse:
        raise M2MReverseNotAllowed

    if need_rebuild_mapping_node(action):
        create_rebuild_user_tree_task_by_asset_perm(instance)


@receiver(m2m_changed, sender=AssetPermission.users.through)
def on_asset_permission_users_changed(sender, action, reverse, pk_set, **kwargs):
    if reverse:
        raise M2MReverseNotAllowed

    if need_rebuild_mapping_node(action):
        UserGrantedTreeRefreshController.add_need_refresh_orgs_for_users(
            [current_org.id], pk_set
        )


@receiver(m2m_changed, sender=AssetPermission.user_groups.through)
def on_asset_permission_user_groups_changed(instance, action, pk_set, model,
                                            reverse, **kwargs):
    if reverse:
        raise M2MReverseNotAllowed

    if need_rebuild_mapping_node(action):
        user_ids = User.groups.through.filter(usergroup_id__in=pk_set).distinct().values_list('user_id', flat=True)
        UserGrantedTreeRefreshController.add_need_refresh_orgs_for_users(
            [current_org.id], user_ids
        )


@receiver(m2m_changed, sender=Asset.nodes.through)
def on_node_asset_change(action, instance, reverse, pk_set, **kwargs):
    if not need_rebuild_mapping_node(action):
        return

    if reverse:
        asset_pk_set = pk_set
        node_pk_set = [instance.id]
    else:
        asset_pk_set = [instance.id]
        node_pk_set = pk_set

    create_rebuild_user_tree_task_by_related_nodes_or_assets.delay(node_pk_set, asset_pk_set)
