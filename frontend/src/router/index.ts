import { createRouter, createWebHistory } from 'vue-router'

const stages = new Set(['script', 'shots', 'storyboards', 'clips', 'final-cut'])

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'studio',
      component: () => import('../views/StudioView.vue'),
    },
    {
      path: '/projects/:projectId/:stage/:shotId?',
      name: 'project-stage',
      component: () => import('../views/StudioView.vue'),
      beforeEnter: (to) => {
        if (typeof to.params.stage === 'string' && stages.has(to.params.stage)) return true
        return {
          name: 'project-stage',
          params: { projectId: to.params.projectId, stage: 'script' },
        }
      },
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
})

export default router
